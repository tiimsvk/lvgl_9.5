"""
LVGL 9.4 Lottie Animation Widget for ESPHome

This module implements the Lottie animation widget for LVGL 9.4.

Lottie is a library for parsing Adobe After Effects animations exported as JSON
using the Bodymovin plugin and rendering them natively.

Requirements:
- LV_USE_LOTTIE must be enabled
- LV_USE_THORVG_INTERNAL must be enabled
- LV_USE_VECTOR_GRAPHIC must be enabled

Usage in ESPHome YAML:

    Method 1 - File on filesystem (SD card, LittleFS):
    - lottie:
        id: my_animation
        src: "/sdcard/animation.json"   # File path on ESP32 filesystem
        width: 200                      # Required for src (can't read at compile time)
        height: 200
        loop: true
        auto_start: true

    Method 2 - Embedded in firmware (auto-detects size from JSON):
    - lottie:
        id: my_animation
        file: "animations/loading.json"  # Local file, embedded in firmware
        loop: true                       # width/height auto-detected from JSON
        auto_start: true

Actions:
    - lvgl.lottie.start: my_animation
    - lvgl.lottie.stop: my_animation
    - lvgl.lottie.pause: my_animation

Note: ThorVG parsing requires a large stack (32KB+). On ESP32, the loading is
deferred to a FreeRTOS task with stack allocated in PSRAM to avoid stack overflow.
"""

import json
from pathlib import Path

from esphome import automation, codegen as cg, config_validation as cv
from esphome.const import CONF_FILE, CONF_HEIGHT, CONF_ID, CONF_RAW_DATA_ID, CONF_WIDTH
from esphome.core import CORE

from ..automation import action_to_code
from ..defines import CONF_AUTO_START, CONF_MAIN, CONF_SRC, literal
from ..helpers import add_lv_use
from ..lv_validation import size
from ..lvcode import lv
from ..types import LvType, ObjUpdateAction
from . import Widget, WidgetType, get_widgets

# Global flag to track if helper code has been added
_lottie_helper_added = False

CONF_LOTTIE = "lottie"
CONF_LOOP = "loop"
CONF_LOTTIE_WIDTH = "lottie_width"
CONF_LOTTIE_HEIGHT = "lottie_height"

lv_lottie_t = LvType("lv_lottie_t")


def lottie_path_validator(value):
    """Validate Lottie source file path (on ESP32 filesystem)."""
    value = cv.string(value)
    if not value.startswith("/"):
        raise cv.Invalid(
            f"Lottie src must be an absolute file path starting with '/', got: '{value}'. "
            f"Example: '/sdcard/animation.json' or '/littlefs/animation.json'"
        )
    if not value.endswith(".json"):
        raise cv.Invalid(
            f"Lottie src must be a JSON file (ending with .json), got: '{value}'"
        )
    return value


def lottie_file_validator(value):
    """Validate and resolve local Lottie file path (to embed in firmware)."""
    value = cv.string(value)
    # Resolve relative to config directory
    path = CORE.relative_config_path(value)
    if not Path(path).is_file():
        raise cv.Invalid(f"Lottie file not found: {path}")
    return str(path)


def validate_lottie_source(config):
    """Validate source and extract dimensions from JSON if using file method."""
    has_src = CONF_SRC in config
    has_file = CONF_FILE in config

    if has_src and has_file:
        raise cv.Invalid("Cannot specify both 'src' and 'file'. Use 'src' for filesystem path or 'file' for embedded.")
    if not has_src and not has_file:
        raise cv.Invalid("Must specify either 'src' (filesystem path) or 'file' (embedded in firmware).")

    # For src method, width and height are required
    if has_src:
        if CONF_WIDTH not in config or CONF_HEIGHT not in config:
            raise cv.Invalid("'width' and 'height' are required when using 'src' (filesystem path). Cannot auto-detect dimensions at compile time.")

    # For file method, auto-detect dimensions from JSON
    if has_file:
        file_path = config[CONF_FILE]
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lottie_data = json.load(f)
                # Extract dimensions from Lottie JSON
                lottie_width = lottie_data.get("w")
                lottie_height = lottie_data.get("h")
                if lottie_width is None or lottie_height is None:
                    raise cv.Invalid(f"Lottie JSON file missing 'w' or 'h' dimensions: {file_path}")
                # Store extracted dimensions
                config[CONF_LOTTIE_WIDTH] = int(lottie_width)
                config[CONF_LOTTIE_HEIGHT] = int(lottie_height)
        except json.JSONDecodeError as e:
            raise cv.Invalid(f"Invalid JSON in Lottie file {file_path}: {e}")
        except Exception as e:
            raise cv.Invalid(f"Error reading Lottie file {file_path}: {e}")

    return config


LOTTIE_SCHEMA = cv.Schema(
    {
        cv.Optional(CONF_WIDTH): size,
        cv.Optional(CONF_HEIGHT): size,
        cv.Optional(CONF_SRC): lottie_path_validator,
        cv.Optional(CONF_FILE): lottie_file_validator,
        cv.Optional(CONF_LOOP, default=True): cv.boolean,
        cv.Optional(CONF_AUTO_START, default=True): cv.boolean,
        cv.GenerateID(CONF_RAW_DATA_ID): cv.declare_id(cg.uint8),
    }
).add_extra(validate_lottie_source)

LOTTIE_MODIFY_SCHEMA = cv.Schema(
    {
        cv.Optional(CONF_SRC): lottie_path_validator,
        cv.Optional(CONF_LOOP): cv.boolean,
    }
)


class LottieType(WidgetType):
    def __init__(self):
        super().__init__(
            CONF_LOTTIE,
            lv_lottie_t,
            (CONF_MAIN,),
            LOTTIE_SCHEMA,
            LOTTIE_MODIFY_SCHEMA,
        )

    def get_uses(self):
        return ("LOTTIE", "THORVG_INTERNAL", "VECTOR_GRAPHIC")

    async def to_code(self, w: Widget, config):
        global _lottie_helper_added

        add_lv_use("LOTTIE")
        add_lv_use("THORVG_INTERNAL")
        add_lv_use("VECTOR_GRAPHIC")

        from ..lvcode import lv_obj, lv_add

        # Get dimensions - either from config or auto-detected from JSON
        if CONF_LOTTIE_WIDTH in config:
            width = config[CONF_LOTTIE_WIDTH]
            height = config[CONF_LOTTIE_HEIGHT]
        else:
            width = config[CONF_WIDTH]
            height = config[CONF_HEIGHT]

        # Set widget size
        lv_obj.set_size(w.obj, width, height)

        # Add FreeRTOS PSRAM task helper code once
        if not _lottie_helper_added:
            _lottie_helper_added = True
            # Add includes and helper struct/function for PSRAM stack task
            cg.add_global(cg.RawExpression("""
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_heap_caps.h"

// Lottie loader task parameters
struct LottieLoadParams {
    lv_obj_t *obj;
    const void *data;
    size_t data_size;
    const char *file_path;
    uint8_t *buffer;
    int width;
    int height;
};

// Stack size for ThorVG parsing (32KB should be enough)
#define LOTTIE_TASK_STACK_SIZE (32 * 1024)

// Task function that loads lottie data with large PSRAM stack
static void lottie_load_task(void *param) {
    LottieLoadParams *p = (LottieLoadParams *)param;

    // Small delay to ensure we're not in middle of LVGL render cycle
    vTaskDelay(pdMS_TO_TICKS(50));

    // Load source data or file (ThorVG parsing happens here - needs large stack)
    if (p->data != nullptr) {
        lv_lottie_set_src_data(p->obj, p->data, p->data_size);
    } else if (p->file_path != nullptr) {
        lv_lottie_set_src_file(p->obj, p->file_path);
    }

    // Set buffer after loading
    lv_lottie_set_buffer(p->obj, p->width, p->height, p->buffer);

    // Free the params struct
    heap_caps_free(param);

    // Delete this task
    vTaskDelete(NULL);
}

// Function to start lottie loading in a task with PSRAM stack
static void lottie_load_async(lv_obj_t *obj, const void *data, size_t data_size,
                               const char *file_path, uint8_t *buffer, int width, int height) {
    // Allocate params in PSRAM
    LottieLoadParams *params = (LottieLoadParams *)heap_caps_malloc(
        sizeof(LottieLoadParams), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (params == nullptr) {
        ESP_LOGE("lottie", "Failed to allocate params in PSRAM");
        return;
    }

    params->obj = obj;
    params->data = data;
    params->data_size = data_size;
    params->file_path = file_path;
    params->buffer = buffer;
    params->width = width;
    params->height = height;

    // Allocate stack in PSRAM
    StackType_t *stack = (StackType_t *)heap_caps_malloc(
        LOTTIE_TASK_STACK_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (stack == nullptr) {
        ESP_LOGE("lottie", "Failed to allocate stack in PSRAM");
        heap_caps_free(params);
        return;
    }

    // Allocate task control block in internal RAM (required by FreeRTOS)
    StaticTask_t *tcb = (StaticTask_t *)heap_caps_malloc(
        sizeof(StaticTask_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (tcb == nullptr) {
        ESP_LOGE("lottie", "Failed to allocate TCB");
        heap_caps_free(params);
        heap_caps_free(stack);
        return;
    }

    // Create task with static allocation (stack in PSRAM)
    TaskHandle_t task = xTaskCreateStatic(
        lottie_load_task,
        "lottie_load",
        LOTTIE_TASK_STACK_SIZE / sizeof(StackType_t),
        params,
        5,  // Priority
        stack,
        tcb
    );

    if (task == nullptr) {
        ESP_LOGE("lottie", "Failed to create lottie load task");
        heap_caps_free(params);
        heap_caps_free(stack);
        heap_caps_free(tcb);
    }
    // Note: stack and tcb will leak when task completes - acceptable for one-shot loading
}
"""))

        # Create unique buffer name using widget id
        widget_id = str(w.obj).replace("->", "_").replace(".", "_")
        buf_name = f"lottie_buf_{widget_id}"

        # Declare static buffer in PSRAM for rendering
        buf_size = width * height * 4
        cg.add_global(cg.RawExpression(
            f"static uint8_t *{buf_name} = nullptr"
        ))

        # Allocate buffer in PSRAM at runtime and start async loading
        if src := config.get(CONF_SRC):
            # File from filesystem
            lv_add(cg.RawStatement(f"""
    {buf_name} = (uint8_t *)heap_caps_malloc({buf_size}, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if ({buf_name} != nullptr) {{
        lottie_load_async({w.obj}, nullptr, 0, "{src}", {buf_name}, {width}, {height});
    }} else {{
        ESP_LOGE("lottie", "Failed to allocate lottie buffer in PSRAM");
    }}"""))
        elif file_path := config.get(CONF_FILE):
            # Embedded data
            with open(file_path, "rb") as f:
                json_data = f.read()

            # Add null terminator
            json_data_with_null = json_data + b'\x00'

            raw_data_id = config[CONF_RAW_DATA_ID]
            prog_arr = cg.progmem_array(raw_data_id, list(json_data_with_null))

            lv_add(cg.RawStatement(f"""
    {buf_name} = (uint8_t *)heap_caps_malloc({buf_size}, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if ({buf_name} != nullptr) {{
        lottie_load_async({w.obj}, {prog_arr}, {len(json_data)}, nullptr, {buf_name}, {width}, {height});
    }} else {{
        ESP_LOGE("lottie", "Failed to allocate lottie buffer in PSRAM");
    }}"""))


lottie_spec = LottieType()


@automation.register_action(
    "lvgl.lottie.start",
    ObjUpdateAction,
    cv.maybe_simple_value(
        {
            cv.Required(CONF_ID): cv.use_id(lv_lottie_t),
        },
        key=CONF_ID,
    ),
)
async def lottie_start(config, action_id, template_arg, args):
    """Start or resume the Lottie animation."""
    widget = await get_widgets(config)

    async def do_start(w: Widget):
        lv.anim_start(lv.lottie_get_anim(w.obj))

    return await action_to_code(widget, do_start, action_id, template_arg, args)


@automation.register_action(
    "lvgl.lottie.stop",
    ObjUpdateAction,
    cv.maybe_simple_value(
        {
            cv.Required(CONF_ID): cv.use_id(lv_lottie_t),
        },
        key=CONF_ID,
    ),
)
async def lottie_stop(config, action_id, template_arg, args):
    """Stop the Lottie animation and reset to beginning."""
    widget = await get_widgets(config)

    async def do_stop(w: Widget):
        lv.anim_delete(w.obj, literal("NULL"))

    return await action_to_code(widget, do_stop, action_id, template_arg, args)


@automation.register_action(
    "lvgl.lottie.pause",
    ObjUpdateAction,
    cv.maybe_simple_value(
        {
            cv.Required(CONF_ID): cv.use_id(lv_lottie_t),
        },
        key=CONF_ID,
    ),
)
async def lottie_pause(config, action_id, template_arg, args):
    """Pause the Lottie animation at current frame."""
    widget = await get_widgets(config)

    async def do_pause(w: Widget):
        lv.anim_delete(w.obj, literal("NULL"))

    return await action_to_code(widget, do_pause, action_id, template_arg, args)
