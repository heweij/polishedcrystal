SECTION "Map Headers", ROMX

INCLUDE "data/maps/maps.asm"


SECTION "Map Attributes", ROMX

INCLUDE "data/maps/attributes.asm"


INCLUDE "data/maps/blocks.asm"
IF DEF(LANG_ZH)
INCLUDE "data/text/zh_maps/scripts.asm"
ELSE
INCLUDE "data/maps/scripts.asm"
ENDC
