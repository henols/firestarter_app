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
# Usage: ./script.sh <json-file> <name>
JSON_FILE='./firestarter/data/database.json'
EPROM_NAME=${1:-W27C512}
TEMP_DIR="./test_data"

# Ensure the temp directory exists, if not create it
if [ ! -d "$TEMP_DIR" ]; then
    mkdir -p "$TEMP_DIR"
    echo "Temporary directory created: $TEMP_DIR"
fi

# Trap to clean up the temporary files on exit or interrupt
trap "rm -rf $TEMP_DIR; echo 'Cleaned up temp files'; exit" EXIT

# Convert TARGET_NAME to uppercase
EPROM_NAME=$(echo "$EPROM_NAME" | tr '[:lower:]' '[:upper:]')

# Use jq to parse JSON and match name, retrieving the corresponding memory-size
MEMORY_SIZE_HEX=$(jq -e --arg target_name "$EPROM_NAME" -r '
  .[] | 
  .[] | 
  select(.name == $target_name) | 
  .["memory-size"]
' "$JSON_FILE")

# Exit if no match is found (MEMORY_SIZE_HEX will be null if no match)
if [ -z "$MEMORY_SIZE_HEX" ]; then
  echo "Error: No match found for name '$EPROM_NAME' in the JSON file."
  exit 1
fi

# Convert the hex value (e.g., 0x40000) to decimal
MEMORY_SIZE_DECIMAL=$((MEMORY_SIZE_HEX))

# Output the result or use it further in your script
echo "Memory size: $MEMORY_SIZE_HEX"

HALF_SIZE=$(( MEMORY_SIZE_DECIMAL / 2 ))

# Generate two random files in the temp directory, each with size HALF_SIZE
dd if=/dev/urandom of="$TEMP_DIR/low_data.bin" bs=1 count=$HALF_SIZE status=none
dd if=/dev/urandom of="$TEMP_DIR/high_data.bin" bs=1 count=$HALF_SIZE status=none

# Concatenate the two files into one file
cat "$TEMP_DIR/low_data.bin" "$TEMP_DIR/high_data.bin" > "$TEMP_DIR/full_data.bin"

# ------------------------------ FIRMWARE TESTS ------------------------------

echo "---------------------------------"
echo "Firmware Version"
echo "---------------------------------"
firestarter fw
if test $? -gt 0
then
	echo "Firmware version failed"
    exit 1
fi
echo
sleep 0.5

# ------------------------------ HARDWARE TESTS ------------------------------

echo "---------------------------------"
echo "Hardware Version"
echo "---------------------------------"
firestarter hw
if test $? -gt 0
then
	echo "Hardware version failed"
    exit 1
fi
echo
sleep 0.5
echo "---------------------------------"
echo "Config"
echo "---------------------------------"
firestarter config
if test $? -gt 0
then
	echo "Config failed"
    exit 1
fi
echo
sleep 1
echo "---------------------------------"
echo "VPP"
echo "---------------------------------"
firestarter vpp -t 5
if test $? -gt 0
then
	echo "VPP failed"
    exit 1
fi
echo
sleep 1
echo "---------------------------------"
echo "VPE"
echo "---------------------------------"
firestarter vpe -t 5
if test $? -gt 0
then
	echo "VPE failed"
    exit 1
fi
echo
sleep 1

# ------------------------------ EPROM TESTS ------------------------------

echo "---------------------------------"
echo "Chip ID - $EPROM_NAME"
echo "---------------------------------"
firestarter id $EPROM_NAME
if test $? -gt 0
then
	echo "Checking Chip ID failed"
    exit 1
fi
echo
sleep 0.5
echo "---------------------------------"
echo "Writing - $EPROM_NAME"
echo "---------------------------------"
firestarter write $EPROM_NAME "$TEMP_DIR/full_data.bin"
if test $? -gt 0
then
	echo "Write failed"
    exit 1
fi
echo
sleep 0.5
echo "---------------------------------"
echo "Verifying"
echo "---------------------------------"
firestarter verify $EPROM_NAME "$TEMP_DIR/full_data.bin"
if test $? -gt 0
then
	echo "Write failed"
    exit 1
fi
echo
sleep 0.5
echo "---------------------------------"
echo "Reading"
echo "---------------------------------"
firestarter read $EPROM_NAME "$TEMP_DIR/read_back.bin"
if test $? -gt 0
then
	echo "Read failed"
    exit 1
fi
echo
colordiff --suppress-common-lines -y  <(xxd "$TEMP_DIR/full_data.bin") <(xxd "$TEMP_DIR/read_back.bin")
if test $? -gt 0
then
	echo "Read back data does not match"
    exit 1
fi
echo "Files are identical"
echo
sleep 0.5
echo "---------------------------------"
echo "Erase"
echo "---------------------------------"
firestarter erase $EPROM_NAME
if test $? -gt 0
then
	echo "Erase failed"
    exit 1
fi
echo
sleep 0.5
echo "---------------------------------"
echo "Blank Check"
echo "---------------------------------"
firestarter blank $EPROM_NAME
if test $? -gt 0
then
	echo "Blank check failed"
    exit 1
fi
echo
# ------------------------------ EPROM INFO TESTS -------------------------
echo "---------------------------------"
echo "Listing all EPROMs"
echo "---------------------------------"
firestarter list
if test $? -gt 0
then
	echo "Search failed"
    exit 1
fi
echo
sleep 0.5
echo "---------------------------------"
echo "Searching for $EPROM_NAME"
echo "---------------------------------"
firestarter search $EPROM_NAME
if test $? -gt 0
then
	echo "Search failed"
    exit 1
fi
echo
sleep 0.5
echo "---------------------------------"
echo "Info for $EPROM_NAME"
echo "---------------------------------"
firestarter info $EPROM_NAME
if test $? -gt 0
then
	echo "Info failed"
    exit 1
fi
echo
echo "All tests passed"