#!/bin/bash

# Usage: ./script.sh <json-file> <name>
JSON_FILE='./firestarter/data/database.json'
EPROM_NAME=${1:-W27C512}
TEMP_DIR="./test_data"

# Ensure the temp directory exists, if not create it
if [ ! -d "$TEMP_DIR" ]; then
    mkdir -p "$TEMP_DIR"
    echo "Temporary directory created: $TEMP_DIR"
fi

# Clean up any existing files in the temp directory from previous runs
rm -f "$TEMP_DIR"/*

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
MEM_SIZE=$((MEMORY_SIZE_HEX))

# Output the result or use it further in your script
echo "Memory size: $MEMORY_SIZE_HEX"

HALF_SIZE=$(( MEM_SIZE / 2 ))

# Generate two random files in the temp directory, each with size HALF_SIZE
dd if=/dev/urandom of="$TEMP_DIR/low_data.bin"  bs=1 count=$HALF_SIZE status=none
dd if=/dev/urandom of="$TEMP_DIR/high_data.bin" bs=1 count=$HALF_SIZE status=none
dd if=/dev/zero    of="$TEMP_DIR/null.bin"      bs=$MEM_SIZE count=1  status=none
< /dev/zero tr '\000' '\377' | head -c $MEM_SIZE > $TEMP_DIR/0xFF.bin


dd if=/dev/zero    of="$TEMP_DIR/null_1k.bin"      bs=1024 count=1  status=none
< /dev/zero tr '\000' '\377' | head -c 1024 > "$TEMP_DIR/0xFF_1k.bin"


# Concatenate the two files into one file
cat "$TEMP_DIR/low_data.bin" "$TEMP_DIR/high_data.bin" > "$TEMP_DIR/full_data.bin"
cat "$TEMP_DIR/null_1k.bin" "$TEMP_DIR/0xFF_1k.bin" "$TEMP_DIR/null_1k.bin" "$TEMP_DIR/0xFF_1k.bin" "$TEMP_DIR/null_1k.bin" "$TEMP_DIR/0xFF_1k.bin" "$TEMP_DIR/null_1k.bin" "$TEMP_DIR/0xFF_1k.bin" > "$TEMP_DIR/mix_8k.bin"

if 0
then
echo "---------------------------------"
echo "Writing mix 8k - $EPROM_NAME"
echo "---------------------------------"
firestarter write $EPROM_NAME "$TEMP_DIR/mix_8k.bin"
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
colordiff --suppress-common-lines -y  <(xxd "$TEMP_DIR/mix_8k.bin") <(xxd "$TEMP_DIR/read_back.bin")
if test $? -gt 0
then
	echo "Read back data does not match"
    exit 1
fi
echo "Files are identical"
echo
sleep 0.5
fi

echo "---------------------------------"
echo "Writing null - $EPROM_NAME"
echo "---------------------------------"
firestarter write $EPROM_NAME "$TEMP_DIR/null.bin"
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
diff --suppress-common-lines -y  <(xxd "$TEMP_DIR/null.bin") <(xxd "$TEMP_DIR/read_back.bin")
if test $? -gt 0 
then
	echo "Read back data does not match"
    exit 1
fi
echo "Files are identical"
echo
sleep 0.5

echo "---------------------------------"
echo "Writing 0xFF - $EPROM_NAME"
echo "---------------------------------"
firestarter write $EPROM_NAME "$TEMP_DIR/0xFF.bin"
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
colordiff --suppress-common-lines -y  <(xxd "$TEMP_DIR/0xFF.bin") <(xxd "$TEMP_DIR/read_back.bin")
if test $? -gt 0
then
	echo "Read back data does not match"
    exit 1
fi
echo "Files are identical"
echo
sleep 0.5
echo "---------------------------------"
echo "Writing full - $EPROM_NAME"
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
echo "---------------------------------"
echo "Writing - parts"
echo "---------------------------------"
firestarter write $EPROM_NAME "$TEMP_DIR/low_data.bin"
if test $? -gt 0
then
	echo "Write low_data.bin failed"
    exit 1
fi
echo
sleep 0.5
firestarter write -b -a $HALF_SIZE $EPROM_NAME "$TEMP_DIR/high_data.bin"
if test $? -gt 0
then
	echo "Write high_data.bin failed"
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
echo "All tests passed"