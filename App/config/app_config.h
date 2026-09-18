/**
 * @file    app_config.h
 * @brief   Single place for every tunable number in the application layer.
 *
 * Phase 1 rule this file exists to enforce: no magic numbers outside here.
 * Anything whose true value depends on analog hardware that has not been built
 * yet is marked UNVERIFIED and is safe to change without touching logic.
 */
#ifndef APP_CONFIG_H
#define APP_CONFIG_H

/* ----------------------------------------------------------------─ identity */

#define FW_VERSION_MAJOR              1U
#define FW_VERSION_MINOR              0U
#define FW_VERSION_PATCH              0U
#define FW_VERSION_STRING             "1.0.0-phase1"
#define PROTOCOL_VERSION              0x01U

/* ------------------------------------------------------------- clock domain */
/* Informational only; the real values live in the generated SystemClock_Config.
 * Referenced by comments and by the sample-rate derivation below.            */
#define TIMER_CLOCK_TIM3_HZ             72000000UL
#define TIM3_PRESCALER                  71U
#define TIM3_AUTO_RELOAD                999U

/** TIM3 update rate = 72e6 / (71+1) / (999+1) = 1000 Hz. Must match .ioc. */
#define ADC_SAMPLE_RATE_HZ              1000U

/** Microseconds between two consecutive frames of the same channel. */
#define ADC_SAMPLE_PERIOD_US            (1000000UL / ADC_SAMPLE_RATE_HZ)

/* --------------------------------------------------------- ADC electricals */

/** Full-scale ADC code count for a 12-bit result. */
#define ADC_FULL_SCALE_CODES            4095U
#define ADC_BITS                        12U

/** Supply at the ADC reference pin, millivolts. Measure before trusting mV. */
#define VDDA_MV                         3300U

/*
 * ECG analog front-end transfer function, as far as the MCU knows it:
 *
 *     V_PA0 = (V_heart * ECG_FRONTEND_GAIN) + ECG_FRONTEND_OFFSET_MV
 *
 * Both constants are UNVERIFIED. The front-end is designed by another project
 * member and is not part of this repository. Until it is measured with a
 * signal generator, the only honest quantities are the raw code and the pin
 * millivolts; neither is a body-surface potential in millivolts.
 */
#define ECG_FRONTEND_GAIN               100.0f  /* UNVERIFIED */
#define ECG_FRONTEND_OFFSET_MV          1650.0f /* UNVERIFIED, assumed mid-rail */
#define ECG_FRONTEND_PARAMS_VERIFIED    0       /* 0 until measured on hardware */

/* --------------------------------------------------- ADC / DMA acquisition */

/**
 * One "frame" is one TIM3 trigger, which converts both ranks: ECG then TEMP.
 * A block is therefore ACQ_FRAMES_PER_BLOCK * 2 half-words, and the length is
 * even by construction - which is what keeps the ECG/temperature interleave
 * aligned across the circular wrap. An odd buffer length would silently swap
 * the two channels at every wrap.
 */
#define ACQ_FRAMES_PER_BLOCK        128U
#define ACQ_BLOCK_COUNT             2U

/** Total DMA ring: 2 blocks, 2 channels per frame, 2 bytes per sample = 1024 B. */
#define ADC_DMA_FRAME_COUNT         (ACQ_FRAMES_PER_BLOCK * ACQ_BLOCK_COUNT)
#define ADC_DMA_WORD_COUNT          (ADC_DMA_FRAME_COUNT * 2U)

/** One millisecond per frame at 1 kHz, so a block is 128 ms of signal. */
#define ACQ_BLOCK_PERIOD_MS         ACQ_FRAMES_PER_BLOCK

/* --------------------------------------------------------------- heartbeat */

/** Normal-range hints used only for the OK / LOW / HIGH status text. */
#define HR_BPM_LOW_DEFAULT              60U
#define HR_BPM_HIGH_DEFAULT             100U

/** Physiological acceptance window; detections outside this RR are rejected. */
#define HR_BPM_MIN_VALID                30U
#define HR_BPM_MAX_VALID                220U

/** Number of most recent valid RR intervals used for the displayed value. */
#define HR_MEDIAN_WINDOW                5U

/* ------------------------------------------------------------- temperature */

/** Application-level temperature update period, milliseconds (requirement <=500). */
#define TEMP_UPDATE_PERIOD_MS           250U

/** Display resolution is 0.1 degC; internal unit is centi-degC (3657 = 36.57). */
#define TEMP_DISPLAY_DECIMALS           1U

/* -------------------------------------------------------------------- UART */

#define UART_BAUD_RATE                  230400UL

/** RX ring buffer size in bytes. Must be a power of two. */
#define UART_RX_RING_SIZE               256U

/** Largest single frame we will ever build or accept: header + max payload + CRC. */
#define PROTOCOL_MAX_PAYLOAD            64U
#define PROTOCOL_HEADER_SIZE            12U
#define PROTOCOL_CRC_SIZE               2U
#define PROTOCOL_MAX_FRAME              (PROTOCOL_HEADER_SIZE + PROTOCOL_MAX_PAYLOAD + PROTOCOL_CRC_SIZE)

/** Blocking TX timeout. A 70-byte frame takes ~3 ms at 230400 8N1. */
#define UART_TX_TIMEOUT_MS              50U

/* -------------------------------------------------------------- scheduler */

/** Main-loop period target, milliseconds. Also the UI tick granularity. */
#define APP_TICK_PERIOD_MS              5U

/** OLED frame interval, milliseconds. 20 ms -> 50 Hz ceiling, see kk_ui_config.h. */
#define UI_FRAME_INTERVAL_MS            40U   /* 25 FPS */

/** STATUS packet period, milliseconds. */
#define DIAG_STATUS_PERIOD_MS           500U

/** TEMP_STATUS packet period, milliseconds. */
#define TEMP_STATUS_PERIOD_MS           500U

#endif /* APP_CONFIG_H */
