from esphome.components.key_provider import KeyProvider
import esphome.config_validation as cv
from esphome.const import CONF_ITEMS, CONF_MODE
from esphome.cpp_generator import MockObj
from esphome.cpp_types import std_string

from ..defines import CONF_MAIN, KEYBOARD_MODES, literal
from ..helpers import add_lv_use, lvgl_components_required
from ..lvcode import lv
from ..types import LvCompound, LvType
from . import Widget, WidgetType, get_widgets
from .textarea import CONF_TEXTAREA, lv_textarea_t

CONF_KEYBOARD = "keyboard"

KEYBOARD_SCHEMA = {
    cv.Optional(CONF_MODE, default="TEXT_UPPER"): KEYBOARD_MODES.one_of,
    cv.Optional(CONF_TEXTAREA): cv.use_id(lv_textarea_t),
}

KEYBOARD_MODIFY_SCHEMA = {
    cv.Optional(CONF_MODE): KEYBOARD_MODES.one_of,
    cv.Optional(CONF_TEXTAREA): cv.use_id(lv_textarea_t),
}

lv_keyboard_t = LvType(
    "LvKeyboardType",
    parents=(KeyProvider, LvCompound),
    largs=[(std_string, "text")],
    has_on_value=True,
    lvalue=lambda w: literal(f"lv_textarea_get_text({w.obj})"),
)


class KeyboardType(WidgetType):
    def __init__(self):
        super().__init__(
            CONF_KEYBOARD,
            lv_keyboard_t,
            (CONF_MAIN, CONF_ITEMS),
            KEYBOARD_SCHEMA,
            modify_schema=KEYBOARD_MODIFY_SCHEMA,
        )

    def get_uses(self):
        return CONF_KEYBOARD, CONF_TEXTAREA

    async def on_create(self, var: MockObj, config: dict):
        # lv_keyboard_create() internally sets LV_ALIGN_BOTTOM_MID alignment and
        # size to LV_PCT(100) x LV_PCT(50). Reset alignment to DEFAULT so that
        # explicit x/y positioning works as absolute coordinates rather than
        # offsets from bottom-center. If the user specifies align: in their config,
        # set_obj_properties() will override this reset.
        lv.call("obj_set_align", var, literal("LV_ALIGN_DEFAULT"))

    async def to_code(self, w: Widget, config: dict):
        lvgl_components_required.add("KEY_LISTENER")
        lvgl_components_required.add(CONF_KEYBOARD)
        add_lv_use("btnmatrix")
        if mode := config.get(CONF_MODE):
            await w.set_property(CONF_MODE, await KEYBOARD_MODES.process(mode))
        if ta := await get_widgets(config, CONF_TEXTAREA):
            await w.set_property(CONF_TEXTAREA, ta[0].obj)


keyboard_spec = KeyboardType()
