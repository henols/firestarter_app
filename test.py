# def display_jumper_configuration(config):
#     # Define the pin labels and their positions
#     jumpers = {
#         "j1": {"5v": "(● ●)● ", "a17": " ●(● ●)"},
#         "j2": {"5v": "(● ●) ●", "a13": " ● ● ●"},
#         "j3": {"32pin": "(●●) ●", "28pin": "● (●●)"},
#     }

#     # Parse the input configuration
#     config_dict = {}
#     for conf in config.split(","):
#         jumper, setting = conf.split("=")
#         config_dict[jumper.strip()] = setting.strip()

#     # Display the jumper settings
#     for jumper, setting in config_dict.items():
#         if jumper in jumpers and setting in jumpers[jumper]:
#             print(f"{jumper}: {jumpers[jumper][setting]}")
#         else:
#             print(f"Invalid configuration: {jumper}={setting}")


# # Example usage:
# configuration = "j1=5v,j2=a13,j3=32pin"
# display_jumper_configuration(configuration)

# // CONTROL REGISTER
VPE_TO_VPP =0x01
A9_VPP_ENABLE= 0x02
VPE_ENABLE= 0x04
P1_VPP_ENABLE =0x08
RW= 0x40
REGULATOR =0x80
A16 =VPE_TO_VPP
A17= 0x10
A18= 0x20

CONTROL_REGISTER = 0xFF

bus_config = {'bus': [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 20], 'rw-pin': 22, 'vpp-pin': 21}
# address = 0x123456


def get_top_address(address):
    top_address = (address >> 16) & (A16 | A17 | A18 | RW)
    top_address |= CONTROL_REGISTER & (A9_VPP_ENABLE | VPE_ENABLE | P1_VPP_ENABLE | REGULATOR);
    return top_address

def remap_address_bus(bus_config,  address, rw) :
    reorg_address = 0
    for i in range(0, len(bus_config['bus'])):
        if bus_config['bus'][i] != 0xFF:
            # reorg_address |= (address & (1 << i)) >> i << bus_config['bus'][i]
            if address & (1 << i):
                reorg_address |= (1 << bus_config['bus'][i])
    # for (int i = 0; i < 19 && config->address_lines[i] != 0xFF; i++) {
    #     if (address & (1 << i)) {
    #         reorg_address |= (1 << config->address_lines[i]);
    #     }
    # }
    if bus_config['rw-pin'] != 0xFF:
        reorg_address |= rw << bus_config['rw-pin']
    # if (config->rw_line != 0xFF) {
    #     reorg_address |= rw << config->rw_line;
    # }
    return reorg_address

def translate(reg, bit):
    return 1 if (reg &  bit) == bit else 0

def print_address_bus(bus_config, address, rw):
    global CONTROL_REGISTER
    print(f'Org address:         {address:06x} {address:024b}' )
    remapped_address=  remap_address_bus(bus_config, address, rw)
    print(f'Remapped address:    {remapped_address:06x} {remapped_address:024b}' )
    CONTROL_REGISTER = 0x00
    top_address_00 = get_top_address(remapped_address)
    print(f'Controle reg (0x00): {top_address_00:02x}     {top_address_00:08b}')
    CONTROL_REGISTER = 0xFF
    top_address_ff = get_top_address(remapped_address)
    print(f'Controle reg (0xFF): {top_address_ff:02x}     {top_address_ff:08b}')
    
    print()
    top_remaped =  remapped_address >> 16 & 0xFF
    print(f'                 0 | F | R')
    print(f'                -----------')
    print(f'A16:             {translate(top_address_00,  A16)} | {translate(top_address_ff , A16)} | {translate(top_remaped, A16)}')
    print(f'A9_VPP_ENABLE:   {translate(top_address_00,  A9_VPP_ENABLE)} | {translate(top_address_ff ,A9_VPP_ENABLE)} | {translate(top_remaped,A9_VPP_ENABLE)}')
    print(f'VPE_ENABLE:      {translate(top_address_00,  VPE_ENABLE)} | {translate(top_address_ff , VPE_ENABLE)} | {translate(top_remaped,VPE_ENABLE)}')
    print(f'P1_VPP_ENABLE:   {translate(top_address_00,  P1_VPP_ENABLE)} | {translate(top_address_ff ,P1_VPP_ENABLE)} | {translate(top_remaped, P1_VPP_ENABLE)}')
    print(f'A17:             {translate(top_address_00, A17)} | {translate(top_address_ff ,A17)} | {translate(top_remaped,A17)}')
    print(f'A18:             {translate(top_address_00, A18)} | {translate(top_address_ff ,A18)} | {translate(top_remaped,A18)}')
    print(f'RW:              {translate(top_address_00,  RW)} | {translate(top_address_ff , RW)} | {translate(top_remaped,RW)}')
    print(f'REGULATOR:       {translate(top_address_00, REGULATOR)} | {translate(top_address_ff , REGULATOR)} | {translate(top_remaped, REGULATOR)}')

print("Bus config: ", bus_config)
print("------------------------------------")
print_address_bus(bus_config, 0x007FFF,0)
print("------------------------------------")
print_address_bus(bus_config, 0x007FFF,1)
print("------------------------------------")
print_address_bus(bus_config, 0x00800F,0)
print("------------------------------------")
print_address_bus(bus_config, 0x00800F,1)
print("------------------------------------")
print_address_bus(bus_config, 0x03100F,1)
# print("------------------------------------")
# print_address_bus(bus_config, 0x0FFFFF,0)
# print("------------------------------------")
# print_address_bus(bus_config, 0x0FFFFF,1)


