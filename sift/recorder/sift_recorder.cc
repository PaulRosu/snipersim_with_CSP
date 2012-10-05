#include <iostream>
#include <fstream>
#include <stdlib.h>
#include <syscall.h>
#include <vector>
#include <deque>
#include <map>

#include <cstdio>
#include <cassert>
#include <unistd.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <strings.h>
#include <sys/syscall.h>
#include <linux/futex.h>
#include <string.h>
#include <pthread.h>

#define __STDC_FORMAT_MACROS
#include <inttypes.h>

#include "pin.H"

#include "sift_writer.h"
#include "bbv_count.h"
#include "../../include/sim_api.h"

//#define DEBUG_OUTPUT 1
#define DEBUG_OUTPUT 0

#define LINE_SIZE_BYTES 64
#define MAX_NUM_SYSCALLS 4096
#define MAX_NUM_THREADS 128

KNOB<string> KnobOutputFile(KNOB_MODE_WRITEONCE, "pintool", "o", "trace", "output");
KNOB<UINT64> KnobBlocksize(KNOB_MODE_WRITEONCE, "pintool", "b", "0", "blocksize");
KNOB<UINT64> KnobUseROI(KNOB_MODE_WRITEONCE, "pintool", "roi", "0", "use ROI markers");
KNOB<UINT64> KnobFastForwardTarget(KNOB_MODE_WRITEONCE, "pintool", "f", "0", "instructions to fast forward");
KNOB<UINT64> KnobDetailedTarget(KNOB_MODE_WRITEONCE, "pintool", "d", "0", "instructions to trace in detail (default = all)");
KNOB<UINT64> KnobEmulateSyscalls(KNOB_MODE_WRITEONCE, "pintool", "e", "0", "emulate syscalls (required for multithreaded applications, default = 0)");
KNOB<UINT64> KnobSiftAppId(KNOB_MODE_WRITEONCE, "pintool", "s", "0", "sift app id (default = 0)");

UINT64 blocksize;
UINT64 fast_forward_target = 0;
UINT64 detailed_target = 0;
PIN_LOCK access_memory_lock;
PIN_LOCK new_threadid_lock;
std::deque<ADDRINT> tidptrs;
BOOL any_thread_in_detail = false;

typedef struct {
   Sift::Writer *output;
   std::deque<ADDRINT> *dyn_address_queue;
   Bbv *bbv;
   ADDRINT bbv_base;
   UINT64 bbv_count;
   UINT64 blocknum;
   UINT64 icount;
   UINT64 icount_detailed;
   UINT32 last_syscall_number;
   UINT32 last_syscall_returnval;
   ADDRINT tid_ptr;
   BOOL in_detail;
   BOOL last_syscall_emulated;
   BOOL running;
   #if defined(TARGET_IA32)
      uint8_t __pad[1];
   #elif defined(TARGET_INTEL64)
      uint8_t __pad[45];
   #endif
} __attribute__((packed)) thread_data_t;
thread_data_t *thread_data;

static_assert((sizeof(thread_data_t) % LINE_SIZE_BYTES) == 0, "Error: Thread data should be a multiple of the line size to prevent false sharing");

bool emulateSyscall[MAX_NUM_SYSCALLS] = {0};
#if defined(TARGET_IA32)
   typedef uint32_t syscall_args_t[6];
#elif defined(TARGET_INTEL64)
   typedef uint64_t syscall_args_t[6];
#endif

void openFile(THREADID threadid);
void closeFile(THREADID threadid);

VOID handleMagic(ADDRINT gax, ADDRINT gbx, ADDRINT gcx)
{
   if (KnobUseROI.Value())
   {
      if (gax == SIM_CMD_ROI_START)
      {
         if (any_thread_in_detail)
         {
            std::cerr << "[SIFT_RECORDER:" << KnobSiftAppId.Value() << "] Error: ROI_START seen, but we have already started." << std::endl;
         }
         else
         {
            std::cerr << "[SIFT_RECORDER:" << KnobSiftAppId.Value() << "] ROI Begin" << std::endl;
         }
         any_thread_in_detail = true;
         for (unsigned int i = 0 ; i < MAX_NUM_THREADS ; i++)
         {
            if (thread_data[i].running && !thread_data[i].in_detail)
               openFile(i);
         }
         PIN_RemoveInstrumentation();
      }
      else if (gax == SIM_CMD_ROI_END)
      {
         std::cerr << "[SIFT_RECORDER:" << KnobSiftAppId.Value() << "] ROI End" << std::endl;
         any_thread_in_detail = false;
         for (unsigned int i = 0 ; i < MAX_NUM_THREADS ; i++)
         {
            if (thread_data[i].running && thread_data[i].in_detail)
               closeFile(i);
         }
         PIN_RemoveInstrumentation();
      }

      for (unsigned int i = 0 ; i < MAX_NUM_THREADS ; i++)
      {
         thread_data[i].in_detail = any_thread_in_detail;
      }
   }
}

VOID countInsns(THREADID threadid, INT32 count)
{
   thread_data[threadid].icount += count;

   if (thread_data[threadid].icount >= fast_forward_target && !KnobUseROI.Value())
   {
      std::cerr << "[SIFT_RECORDER:" << KnobSiftAppId.Value() << ":" << threadid << "] Changing to detailed after " << thread_data[threadid].icount << " instructions" << std::endl;
      if (!thread_data[threadid].in_detail)
         openFile(threadid);
      thread_data[threadid].in_detail = true;
      thread_data[threadid].icount = 0;
      any_thread_in_detail = true;
      PIN_RemoveInstrumentation();
   }
}

VOID sendInstruction(THREADID threadid, ADDRINT addr, UINT32 size, UINT32 num_addresses, BOOL is_branch, BOOL taken, BOOL is_predicate, BOOL executing)
{
   // We're still called for instructions in the same basic block as ROI end, ignore these
   if (!thread_data[threadid].in_detail)
      return;

   ++thread_data[threadid].icount;
   ++thread_data[threadid].icount_detailed;

   if (thread_data[threadid].bbv_base == 0)
   {
      thread_data[threadid].bbv_base = addr; // We're the start of a new basic block
   }
   thread_data[threadid].bbv_count++;
   if (is_branch)
   {
      thread_data[threadid].bbv->count(thread_data[threadid].bbv_base, thread_data[threadid].bbv_count);
      thread_data[threadid].bbv_base = 0; // Next instruction starts a new basic block
      thread_data[threadid].bbv_count = 0;
   }

   uint64_t addresses[Sift::MAX_DYNAMIC_ADDRESSES] = { 0 };
   for(uint8_t i = 0; i < num_addresses; ++i)
   {
      addresses[i] = thread_data[threadid].dyn_address_queue->front();
      assert(!thread_data[threadid].dyn_address_queue->empty());
      thread_data[threadid].dyn_address_queue->pop_front();
   }
   assert(thread_data[threadid].dyn_address_queue->empty());

   thread_data[threadid].output->Instruction(addr, size, num_addresses, addresses, is_branch, taken, is_predicate, executing);

   if (detailed_target != 0 && thread_data[threadid].icount_detailed >= detailed_target)
   {
      thread_data[threadid].in_detail = false;
      closeFile(threadid);
      PIN_Detach();
      return;
   }

   if (blocksize && thread_data[threadid].icount >= blocksize)
   {
      openFile(threadid);
      thread_data[threadid].icount = 0;
   }
}

VOID handleMemory(THREADID threadid, ADDRINT address)
{
   // We're still called for instructions in the same basic block as ROI end, ignore these
   if (!thread_data[threadid].in_detail)
      return;

   thread_data[threadid].dyn_address_queue->push_back(address);
}

UINT32 addMemoryModeling(INS ins)
{
   UINT32 num_addresses = 0;

   if (INS_IsMemoryRead (ins) || INS_IsMemoryWrite (ins))
   {
      for (unsigned int i = 0; i < INS_MemoryOperandCount(ins); i++)
      {
         INS_InsertCall(ins, IPOINT_BEFORE,
               AFUNPTR(handleMemory),
               IARG_THREAD_ID,
               IARG_MEMORYOP_EA, i,
               IARG_END);
         num_addresses++;
      }
   }
   assert(num_addresses <= Sift::MAX_DYNAMIC_ADDRESSES);

   return num_addresses;
}

VOID insertCall(INS ins, IPOINT ipoint, UINT32 num_addresses, BOOL is_branch, BOOL taken)
{
   INS_InsertCall(ins, ipoint,
      AFUNPTR(sendInstruction),
      IARG_THREAD_ID,
      IARG_ADDRINT, INS_Address(ins),
      IARG_UINT32, UINT32(INS_Size(ins)),
      IARG_UINT32, num_addresses,
      IARG_BOOL, is_branch,
      IARG_BOOL, taken,
      IARG_BOOL, INS_IsPredicated(ins),
      IARG_EXECUTING,
      IARG_END);
}

// Emulate all system calls
// Do this as a regular callback (versus syscall enter/exit functions) as those hold the global pin lock
VOID emulateSyscallFunc(THREADID threadid, CONTEXT *ctxt)
{
   ADDRINT syscall_number = PIN_GetContextReg(ctxt, REG_GAX);

   assert(syscall_number < MAX_NUM_SYSCALLS);

   syscall_args_t args;
   #if defined(TARGET_IA32)
      args[0] = PIN_GetContextReg(ctxt, LEVEL_BASE::REG_GBX);
      args[1] = PIN_GetContextReg(ctxt, LEVEL_BASE::REG_GCX);
      args[2] = PIN_GetContextReg(ctxt, LEVEL_BASE::REG_GDX);
      args[3] = PIN_GetContextReg(ctxt, LEVEL_BASE::REG_GSI);
      args[4] = PIN_GetContextReg(ctxt, LEVEL_BASE::REG_GDI);
      args[5] = PIN_GetContextReg(ctxt, LEVEL_BASE::REG_GBP);
   #elif defined(TARGET_INTEL64)
      args[0] = PIN_GetContextReg(ctxt, LEVEL_BASE::REG_GDI);
      args[1] = PIN_GetContextReg(ctxt, LEVEL_BASE::REG_GSI);
      args[2] = PIN_GetContextReg(ctxt, LEVEL_BASE::REG_GDX);
      args[3] = PIN_GetContextReg(ctxt, LEVEL_BASE::REG_R10);
      args[4] = PIN_GetContextReg(ctxt, LEVEL_BASE::REG_R8);
      args[5] = PIN_GetContextReg(ctxt, LEVEL_BASE::REG_R9);
   #else
      #error "Unknown target architecture, require either TARGET_IA32 or TARGET_INTEL64"
   #endif

   if (syscall_number == SYS_write)
   {
      int fd = (int)args[0];
      const char *buf = (const char*)args[1];
      size_t count = (size_t)args[2];

      if (count > 0 && (fd == 1 || fd == 2))
         thread_data[threadid].output->Output(fd, buf, count);

      thread_data[threadid].last_syscall_emulated = false;
   }
   // Handle SYS_clone child tid capture for proper pthread_join emulation.
   // When the CLONE_CHILD_CLEARTID option is enabled, remember its child_tidptr and
   // then when the thread ends, write 0 to the tid mutex and futex_wake it
   else if (syscall_number == SYS_clone)
   {
      thread_data[threadid].output->NewThread();
      // Store the thread's tid ptr for later use
      #if defined(TARGET_IA32)
         ADDRINT tidptr = args[2];
      #elif defined(TARGET_INTEL64)
         ADDRINT tidptr = args[3];
      #endif
      GetLock(&new_threadid_lock, threadid);
      tidptrs.push_back(tidptr);
      ReleaseLock(&new_threadid_lock);
   }
   else if (emulateSyscall[syscall_number])
   {
      thread_data[threadid].last_syscall_number = syscall_number;
      thread_data[threadid].last_syscall_emulated = true;
      thread_data[threadid].last_syscall_returnval = thread_data[threadid].output->Syscall(syscall_number, (char*)args, sizeof(args));
   }
   else
   {
      thread_data[threadid].last_syscall_emulated = false;
   }
}

VOID traceCallback(TRACE trace, void *v)
{
   BBL bbl_head = TRACE_BblHead(trace);

   for (BBL bbl = bbl_head; BBL_Valid(bbl); bbl = BBL_Next(bbl))
   {
      for(INS ins = BBL_InsHead(bbl); ; ins = INS_Next(ins))
      {
         // Simics-style magic instruction: xchg bx, bx
         if (INS_IsXchg(ins) && INS_OperandReg(ins, 0) == REG_BX && INS_OperandReg(ins, 1) == REG_BX)
         {
            INS_InsertPredicatedCall(ins, IPOINT_BEFORE, (AFUNPTR)handleMagic, IARG_REG_VALUE, REG_GAX, IARG_REG_VALUE, REG_GBX, IARG_REG_VALUE, REG_GCX, IARG_END);
         }

         if (ins == BBL_InsTail(bbl))
            break;
      }

      if (!any_thread_in_detail)
      {
         BBL_InsertCall(bbl, IPOINT_ANYWHERE, (AFUNPTR)countInsns, IARG_THREAD_ID, IARG_UINT32, BBL_NumIns(bbl), IARG_END);
      }
      else
      {
         for(INS ins = BBL_InsHead(bbl); ; ins = INS_Next(ins))
         {
            // For memory instructions, we should populate data items before we send the MicroOp
            UINT32 num_addresses = addMemoryModeling(ins);

            bool is_branch = INS_IsBranch(ins) && INS_HasFallThrough(ins);

            if (is_branch)
            {
               insertCall(ins, IPOINT_AFTER,        num_addresses, true  /* is_branch */, false /* taken */);
               insertCall(ins, IPOINT_TAKEN_BRANCH, num_addresses, true  /* is_branch */, true  /* taken */);
            }
            else
               insertCall(ins, IPOINT_BEFORE,       num_addresses, false /* is_branch */, false /* taken */);

            // Handle emulated syscalls
            if (KnobEmulateSyscalls.Value())
            {
               if (INS_IsSyscall(ins))
               {
                  INS_InsertPredicatedCall
                  (
                     ins,
                     IPOINT_BEFORE,
                     AFUNPTR(emulateSyscallFunc),
                     IARG_THREAD_ID,
                     IARG_CONST_CONTEXT,
                     IARG_END
                  );
               }
            }

            if (ins == BBL_InsTail(bbl))
               break;
         }
      }
   }
}

VOID syscallEntryCallback(THREADID threadid, CONTEXT *ctxt, SYSCALL_STANDARD syscall_standard, VOID *v)
{
   if (!thread_data[threadid].last_syscall_emulated)
   {
      return;
   }

   PIN_SetSyscallNumber(ctxt, syscall_standard, SYS_getpid);
}

VOID syscallExitCallback(THREADID threadid, CONTEXT *ctxt, SYSCALL_STANDARD syscall_standard, VOID *v)
{
   if (!thread_data[threadid].last_syscall_emulated)
   {
      return;
   }

   PIN_SetContextReg(ctxt, REG_GAX, thread_data[threadid].last_syscall_returnval);
   thread_data[threadid].last_syscall_emulated = false;
}

VOID Fini(INT32 code, VOID *v)
{
   for (unsigned int i = 0 ; i < MAX_NUM_THREADS ; i++)
   {
      if (thread_data[i].output)
      {
         closeFile(i);
      }
   }
}

VOID Detach(VOID *v)
{
}

void getCode(uint8_t *dst, const uint8_t *src, uint32_t size)
{
   PIN_SafeCopy(dst, src, size);
}

void handleAccessMemory(void *arg, Sift::MemoryLockType lock_signal, Sift::MemoryOpType mem_op, uint64_t d_addr, uint8_t* data_buffer, uint32_t data_size)
{
   // Lock memory globally if requested
   // This operation does not occur very frequently, so this should not impact performance
   if (lock_signal == Sift::MemLock)
   {
      GetLock(&access_memory_lock, 0);
   }

   if (mem_op == Sift::MemRead)
   {
      // The simulator is requesting data from us
      memcpy(data_buffer, reinterpret_cast<void*>(d_addr), data_size);
   }
   else if (mem_op == Sift::MemWrite)
   {
      // The simulator is requesting that we write data back to memory
      memcpy(reinterpret_cast<void*>(d_addr), data_buffer, data_size);
   }
   else
   {
      std::cerr << "Error: invalid memory operation type" << std::endl;
      assert(false);
   }

   if (lock_signal == Sift::MemUnlock)
   {
      ReleaseLock(&access_memory_lock);
   }
}

void openFile(THREADID threadid)
{
   if (thread_data[threadid].output)
   {
      closeFile(threadid);
      ++thread_data[threadid].blocknum;
   }

   if (threadid != 0)
   {
      assert(KnobEmulateSyscalls.Value() != 0);
   }

   char filename[1024] = {0};
   char response_filename[1024] = {0};
   if (KnobEmulateSyscalls.Value() == 0)
   {
      if (blocksize)
         sprintf(filename, "%s.%" PRIu64 ".sift", KnobOutputFile.Value().c_str(), thread_data[threadid].blocknum);
      else
         sprintf(filename, "%s.sift", KnobOutputFile.Value().c_str());
   }
   else
   {
      if (blocksize)
         sprintf(filename, "%s.%" PRIu64 ".app%" PRIu64 ".th%" PRIu64 ".sift", KnobOutputFile.Value().c_str(), thread_data[threadid].blocknum, KnobSiftAppId.Value(), (UINT64)threadid);
      else
         sprintf(filename, "%s.app%" PRIu64 ".th%" PRIu64 ".sift", KnobOutputFile.Value().c_str(), KnobSiftAppId.Value(), (UINT64)threadid);
   }

   std::cerr << "[SIFT_RECORDER:" << KnobSiftAppId.Value() << ":" << threadid << "] Output = [" << filename << "]" << std::endl;

   if (KnobEmulateSyscalls.Value())
   {
      sprintf(response_filename, "%s_response.app%" PRIu64 ".th%" PRIu64 ".sift", KnobOutputFile.Value().c_str(), KnobSiftAppId.Value(), (UINT64)threadid);
      std::cerr << "[SIFT_RECORDER:" << KnobSiftAppId.Value() << ":" << threadid << "] Response = [" << response_filename << "]" << std::endl;
   }


   // Open the file for writing
   try {
      #ifdef TARGET_IA32
         const bool arch32 = true;
      #else
         const bool arch32 = false;
      #endif
      thread_data[threadid].output = new Sift::Writer(filename, getCode, false, response_filename, threadid, arch32);
   } catch (...) {
      std::cerr << "[SIFT_RECORDER:" << KnobSiftAppId.Value() << ":" << threadid << "] Error: Unable to open the output file " << filename << std::endl;
      exit(1);
   }

   thread_data[threadid].output->setHandleAccessMemoryFunc(handleAccessMemory, reinterpret_cast<void*>(threadid));
}

void closeFile(THREADID threadid)
{
   if (thread_data[threadid].output)
      thread_data[threadid].output->End();

   std::cerr << "[SIFT_RECORDER:" << KnobSiftAppId.Value() << ":" << threadid << "] Recorded " << thread_data[threadid].icount_detailed;
   if (thread_data[threadid].icount > thread_data[threadid].icount_detailed)
      std::cerr << " (out of " << thread_data[threadid].icount << ")";
   std::cerr << " instructions" << std::endl;
   delete thread_data[threadid].output;
   thread_data[threadid].output = NULL;

   if (blocksize)
   {
      if (thread_data[threadid].bbv_count)
      {
         thread_data[threadid].bbv->count(thread_data[threadid].bbv_base, thread_data[threadid].bbv_count);
         thread_data[threadid].bbv_base = 0; // Next instruction starts a new basic block
         thread_data[threadid].bbv_count = 0;
      }

      char filename[1024];
      sprintf(filename, "%s.%" PRIu64 ".bbv", KnobOutputFile.Value().c_str(), thread_data[threadid].blocknum);

      FILE *fp = fopen(filename, "w");
      fprintf(fp, "%" PRIu64 "\n", thread_data[threadid].bbv->getInstructionCount());
      for(int i = 0; i < Bbv::NUM_BBV; ++i)
         fprintf(fp, "%" PRIu64 "\n", thread_data[threadid].bbv->getDimension(i) / thread_data[threadid].bbv->getInstructionCount());
      fclose(fp);

      thread_data[threadid].bbv->clear();
   }
}

// The thread that watched this new thread start is responsible for setting up the connection with the simulator
VOID threadStart(THREADID threadid, CONTEXT *ctxt, INT32 flags, VOID *v)
{
   assert(thread_data[threadid].bbv == NULL);
   assert(thread_data[threadid].dyn_address_queue == NULL);

   // The first thread (master) doesn't need to join with anyone else
   GetLock(&new_threadid_lock, threadid);
   if (tidptrs.size() > 0)
   {
      thread_data[threadid].tid_ptr = tidptrs.front();
      tidptrs.pop_front();
   }
   ReleaseLock(&new_threadid_lock);

   thread_data[threadid].bbv = new Bbv();
   thread_data[threadid].dyn_address_queue = new std::deque<ADDRINT>();

   if (any_thread_in_detail)
   {
      openFile(threadid);
      thread_data[threadid].in_detail = true;
   }

   thread_data[threadid].running = true;
}

VOID threadFinishHelper(VOID *arg)
{
   uint64_t threadid = reinterpret_cast<uint64_t>(arg);
   if (thread_data[threadid].tid_ptr)
   {
      // Set this pointer to 0 to indicate that this thread is complete
      intptr_t tid = (intptr_t)thread_data[threadid].tid_ptr;
      *(int*)tid = 0;
      // Send the FUTEX_WAKE to the simulator to wake up a potential pthread_join() caller
      syscall_args_t args = {0};
      args[0] = (intptr_t)tid;
      args[1] = FUTEX_WAKE;
      args[2] = 1;
      thread_data[threadid].output->Syscall(SYS_futex, (char*)args, sizeof(args));
   }

   delete thread_data[threadid].dyn_address_queue;
   delete thread_data[threadid].bbv;

   thread_data[threadid].dyn_address_queue = NULL;
   thread_data[threadid].bbv = NULL;

   if (thread_data[threadid].in_detail)
   {
      thread_data[threadid].in_detail = false;
      closeFile(threadid);
   }
}

VOID threadFinish(THREADID threadid, const CONTEXT *ctxt, INT32 flags, VOID *v)
{
#if DEBUG_OUTPUT
   std::cerr << "[SIFT_RECORDER:" << KnobSiftAppId.Value() << ":" << threadid << "] Finish Thread" << std::endl;
#endif

   thread_data[threadid].running = false;

   // To prevent deadlocks during simulation, start a new thread to handle this thread's
   // cleanup.  This is needed because this function could be called in the context of
   // another thread, creating a deadlock scenario.
   PIN_SpawnInternalThread(threadFinishHelper, (VOID*)(unsigned long)threadid, 0, NULL);
}

int main(int argc, char **argv)
{
   if (PIN_Init(argc,argv)) {
      std::cerr << "Error, invalid parameters" << std::endl;
      exit(1);
   }
   PIN_InitSymbols();

   size_t thread_data_size = MAX_NUM_THREADS * sizeof(*thread_data);
   if (posix_memalign((void**)&thread_data, LINE_SIZE_BYTES, thread_data_size) != 0)
   {
      std::cerr << "Error, posix_memalign() failed" << std::endl;
      exit(1);
   }
   bzero(thread_data, thread_data_size);

   InitLock(&access_memory_lock);
   InitLock(&new_threadid_lock);

   blocksize = KnobBlocksize.Value();
   fast_forward_target = KnobFastForwardTarget.Value();
   detailed_target = KnobDetailedTarget.Value();
   if (fast_forward_target == 0 && !KnobUseROI.Value())
   {
      for (unsigned int i = 0 ; i < MAX_NUM_THREADS ; i++)
      {
         thread_data[i].in_detail = true;
         any_thread_in_detail = true;
      }
   }

   if (KnobEmulateSyscalls.Value())
   {
      emulateSyscall[SYS_futex] = true;
      PIN_AddSyscallEntryFunction(syscallEntryCallback, 0);
      PIN_AddSyscallExitFunction(syscallExitCallback, 0);
   }

   TRACE_AddInstrumentFunction(traceCallback, 0);
   PIN_AddThreadStartFunction(threadStart, 0);
   PIN_AddThreadFiniFunction(threadFinish, 0);
   PIN_AddFiniFunction(Fini, 0);
   PIN_AddDetachFunction(Detach, 0);

   PIN_StartProgram();

   return 0;
}