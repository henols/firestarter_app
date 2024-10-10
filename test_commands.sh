echo "---------------------------------"
echo "Writing"
echo "---------------------------------"
firestarter write w27c512 64k-random.bin
if test $? -gt 0
then
	echo "Write failed"
    exit 1
fi
echo
sleep 3
echo "---------------------------------"
echo "Reading"
echo "---------------------------------"
firestarter read w27c512
if test $? -gt 0
then
	echo "Read failed"
    exit 1
fi
echo
diff -s 64k-random.bin W27C512.bin
if test $? -gt 0
then
    exit 1
fi
echo
sleep 3
echo "---------------------------------"
echo "Erase"
echo "---------------------------------"
firestarter erase w27c512
if test $? -gt 0
then
	echo "Erase failed"
    exit 1
fi
echo
sleep 3
echo "---------------------------------"
echo "Blank Check"
echo "---------------------------------"
firestarter blank w27c512
if test $? -gt 0
then
	echo "Blank check failed"
    exit 1
fi
echo
sleep 3
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
sleep 3
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
sleep 3
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
echo "All tests passed"