echo "---------------------------------"
echo "Writing"
echo "---------------------------------"
firestarter write w27c512 64k-random.bin
echo
sleep 3
echo "---------------------------------"
echo "Reading"
echo "---------------------------------"
firestarter read w27c512
echo
diff -s 64k-random.bin W27C512.bin
echo
sleep 3
echo "---------------------------------"
echo "Erase"
echo "---------------------------------"
firestarter erase w27c512
echo
sleep 3
echo "---------------------------------"
echo "Blank Check"
echo "---------------------------------"
firestarter blank w27c512
echo
sleep 3
echo "---------------------------------"
echo "Hardware Version"
echo "---------------------------------"
firestarter hw
echo
sleep 3
echo "---------------------------------"
echo "Firmware Version"
echo "---------------------------------"
firestarter fw
echo
sleep 3
echo "---------------------------------"
echo "Config"
echo "---------------------------------"
firestarter config
echo