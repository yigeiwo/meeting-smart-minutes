# PRIV_REQUIRES esp_timer: 仅供 bsp_button.c 内部做分压标定的周期采样, 不暴露到公共头文件。
# 漏掉它会在编译期直接报 "esp_timer.h: No such file or directory"。
idf_component_register(
    SRCS "src/bsp_i2c.c"
         "src/bsp_display.c"
         "src/bsp_display_lvgl.c"
         "src/bsp_button.c"
         "src/bsp_audio.c"
         "src/bsp_battery.c"
    INCLUDE_DIRS "include"
    REQUIRES driver esp_driver_i2c esp_driver_i2s esp_lcd esp_lvgl_port button esp_adc esp_codec_dev
    PRIV_REQUIRES esp_timer
)
