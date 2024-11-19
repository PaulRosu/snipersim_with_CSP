NUM_CORES=8
RES_FOLDER=$(pwd)/res

mkdir -p $RES_FOLDER

../../run-sniper -v -n $NUM_CORES -c gainestown -d $RES_FOLDER \
    -s acaps_scsp:2000:1000:2 \
    --power \
    -- ./fft -p $NUM_CORES
    
 