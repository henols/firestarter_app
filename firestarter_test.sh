#!/bin/bash
#
# ----------------------------------------------------------
#
# Project Name: Firestarter
# Copyright (c) 2025 Henrik Olsson
#
# Permission is hereby granted under MIT license.
#
# ----------------------------------------------------------
#
EPROM_NAME=${1:-W27C512}

# DEFAULT_ARGS="-f"
VERBOSE=0

ONLY_EPROM_TESTS=0

FIRMWARE_TESTS=1
HARDWARE_TESTS=1
EPROM_TESTS=1
INFO_TESTS=1

if test $ONLY_EPROM_TESTS -eq 1; then
  FIRMWARE_TESTS=0
    HARDWARE_TESTS=0
    INFO_TESTS=0
fi
CLEAN_UP=1

JSON_FILE='./firestarter/data/database_generated.json'
TEMP_DIR="./test_data"

if test $EPROM_TESTS -eq 1; then
    # Ensure the temp directory exists, if not create it
    if [ ! -d "$TEMP_DIR" ]; then
        mkdir -p "$TEMP_DIR"
        # echo "Temporary directory created: $TEMP_DIR"
    fi

    if test $CLEAN_UP -eq 1; then
        # Trap to clean up the temporary files on exit or interrupt
        trap "rm -rf $TEMP_DIR; echo 'Cleaned up temp files'; exit" EXIT
    fi
    # Convert TARGET_NAME to uppercase
    EPROM_NAME=$(echo "$EPROM_NAME" | tr '[:lower:]' '[:upper:]')

    # Use jq to parse JSON and match name, retrieving the corresponding memory-size
    MEMORY_SIZE_HEX=$(jq -e --arg target_name "$EPROM_NAME" -r '
  .[] | 
  .[] | 
  select(.name == $target_name) | 
  .["memory-size"]
' "$JSON_FILE")

    HAS_CHIP_ID=$(jq -e --arg target_name "$EPROM_NAME" -r '
  .[] | 
  .[] | 
  select(.name == $target_name) | 
  .["has-chip-id"]
' "$JSON_FILE")

    CAN_ERASE=$(jq -e --arg target_name "$EPROM_NAME" -r '
  .[] | 
  .[] | 
  select(.name == $target_name) | 
  .["can-erase"]
' "$JSON_FILE")

    # Exit if no match is found (MEMORY_SIZE_HEX will be null if no match)
    if [ -z "$MEMORY_SIZE_HEX" ]; then
        echo "Error: No match found for name '$EPROM_NAME' in the JSON file."
        exit 1
    fi

    # Convert the hex value (e.g., 0x40000) to decimal
    MEMORY_SIZE_DECIMAL=$((MEMORY_SIZE_HEX))

    HALF_SIZE=$((MEMORY_SIZE_DECIMAL / 2))

    # Generate two random files in the temp directory, each with size HALF_SIZE
    dd if=/dev/urandom of="$TEMP_DIR/low_data.bin" bs=1 count=$HALF_SIZE status=none
    dd if=/dev/urandom of="$TEMP_DIR/high_data.bin" bs=1 count=$HALF_SIZE status=none

    # Concatenate the two files into one file
    cat "$TEMP_DIR/low_data.bin" "$TEMP_DIR/high_data.bin" >"$TEMP_DIR/full_data.bin"
fi

exec_firestarter() {
    TEST_NAME=$1
    CMD_NAME=$2
    EPROM=${3:-}
    if [ -n "$3" ]; then
        EPROM="$3"
    fi
    FILE_NAME=${4:-}
    if [ -n "$4" ]; then
        FILE_NAME=$TEMP_DIR/$4
    fi
    CMD_ARGS=${5:-}
    if test $VERBOSE -eq 1; then
        VERBOSE_FLAG="--verbose"
    fi
    firestarter_cmd="firestarter $VERBOSE_FLAG $CMD_NAME $CMD_ARGS $EPROM $FILE_NAME"
    echo "---------------------------------"
    echo "Test: $TEST_NAME"
    echo "Cmd: $firestarter_cmd"
    echo "---------------------------------"
    echo
    $firestarter_cmd
    if test $? -gt 0; then
        echo "$TEST_NAME failed"
        exit 1
    fi
    echo
    sleep 0.5
}

echo "Firestarter Python Application"
firestarter --version
echo "Eprom: $EPROM_NAME, memory size: $MEMORY_SIZE_HEX"
echo

# ------------------------------ FIRMWARE TESTS ------------------------------
if test $FIRMWARE_TESTS -eq 1; then
    exec_firestarter "Firmware Version" "fw"
fi
# ------------------------------ HARDWARE TESTS ------------------------------
if test $HARDWARE_TESTS -eq 1; then
    exec_firestarter "Hardware Version" "hw"
    exec_firestarter "Config" "config"
    exec_firestarter "VPP" "vpp" "" "" "-t 5"
    exec_firestarter "VPE" "vpe" "" "" "-t 5"
fi

# ------------------------------ EPROM TESTS ------------------------------
if test $EPROM_TESTS -eq 1; then
    if test $HAS_CHIP_ID == true; then
        exec_firestarter "$EPROM_NAME Chip ID" "id" $EPROM_NAME "" "$DEFAULT_ARGS"
    else
        echo "---------------------------------"
        echo "Chip ID not supported, skipping test"
        echo
    fi
    exec_firestarter "Writing to $EPROM_NAME" write $EPROM_NAME "full_data.bin" "$DEFAULT_ARGS"
    exec_firestarter "Verifying data in $EPROM_NAME" verify $EPROM_NAME full_data.bin "$DEFAULT_ARGS"
    exec_firestarter "Reading from $EPROM_NAME" read $EPROM_NAME "read_back.bin" "$DEFAULT_ARGS"
    colordiff --suppress-common-lines -y <(xxd "$TEMP_DIR/full_data.bin") <(xxd "$TEMP_DIR/read_back.bin")
    if test $? -gt 0; then
        echo "Read back data does not match"
        exit 1
    fi
    echo "Files are identical"
    if test $CAN_ERASE == true; then
        exec_firestarter "Erasing $EPROM_NAME" erase $EPROM_NAME "" "$DEFAULT_ARGS"
        exec_firestarter "Blank checking $EPROM_NAME" blank $EPROM_NAME "" "$DEFAULT_ARGS"
    else
        echo "---------------------------------"
        echo "Erase not supported, skipping erase and blank test"
        echo
    fi
fi

# ------------------------------ EPROM INFO TESTS -------------------------
if test $INFO_TESTS -eq 1; then
    exec_firestarter "Listing all EPROMs" list
    exec_firestarter "Searching for $EPROM_NAME" search $EPROM_NAME
    exec_firestarter "Info for $EPROM_NAME" info $EPROM_NAME
fi
echo "All tests passed"
