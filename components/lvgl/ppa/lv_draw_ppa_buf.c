/**
 * @file lv_draw_ppa_buf.c
 * Fixed PPA buffer cache handling for LVGL 9.4 on ESP32-P4
 * Backported from https://github.com/lvgl/lvgl/pull/9162
 * Adapted for C++ compilation (ESPHome build system)
 *
 * NOTE: We do NOT set the global invalidate_cache_cb handler because
 * it would affect ALL draw operations (software renderer included).
 * esp_cache_msync crashes if called on non-cacheable or unaligned memory.
 * Instead, cache sync is done directly in ppa_execute_drawing().
 */

#include "sdkconfig.h"
#ifdef CONFIG_SOC_PPA_SUPPORTED

#include "lv_draw_ppa_private.h"
#include "lv_draw_ppa.h"

/**********************
 *   GLOBAL FUNCTIONS
 **********************/
void lv_draw_buf_ppa_init_handlers(void)
{
    /* Intentionally empty.
     * We do NOT set the global invalidate_cache_cb because it would be
     * called for ALL draw buffers, including software renderer ones
     * that may not be in cache-aligned PSRAM.
     * Cache operations are done directly in ppa_execute_drawing(). */
}

void lv_draw_ppa_cache_sync(lv_draw_buf_t * buf)
{
    if(buf == NULL || buf->data == NULL || buf->data_size == 0) return;
    esp_cache_msync(buf->data, buf->data_size,
                    ESP_CACHE_MSYNC_FLAG_DIR_C2M | ESP_CACHE_MSYNC_FLAG_TYPE_DATA);
}

#endif /* CONFIG_SOC_PPA_SUPPORTED */
