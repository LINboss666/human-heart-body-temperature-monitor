/**
 * @file    acquisition.h
 * @brief   ADC1 + TIM3 + DMA1_Channel1 hardware-triggered sample source.
 *
 * The chain is entirely hardware: TIM3 overflows at 1 kHz, its TRGO pulses the
 * ADC's external trigger, the ADC scans channel 0 then channel 1, and the DMA
 * moves each result into the ring. No ISR performs sampling and none is needed
 * to sustain it; the DMA completion interrupts only say "a block is ready".
 *
 * Because of that, the interrupt handlers here must stay minimal. Everything
 * that costs time - filtering, heart-rate detection, the display, the UART - is
 * done in App_Loop, never in a callback.
 */
#ifndef ACQUISITION_H
#define ACQUISITION_H

#include <stdbool.h>
#include <stdint.h>

#include "app_config.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Bind to the CubeMX handles. Does not start any hardware. */
void acquisition_init(void);

/**
 * Arm and run the chain, in the order the hardware requires:
 * calibration (which needs the ADC disabled and leaves it enabled), then the
 * DMA transfer, then the timer whose TRGO triggers each conversion. Starting the
 * timer first would trigger conversions before the DMA was listening.
 */
bool acquisition_start(void);

/**
 * Hand the oldest ready block to the caller.
 *
 * @param[out] words    points into the internal DMA ring, valid only until the
 *                      next call that consumes the other block; copy before use
 * @param[out] frames   always ACQ_FRAMES_PER_BLOCK
 * @param[out] first    absolute frame index of words[0], so the consumer can
 *                      detect a dropped block and keep a correct time axis
 * @return true when a block was returned
 */
bool acquisition_take_block(const uint16_t **words, uint16_t *frames,
                            uint32_t *first_frame_index);

/** Blocks produced but never consumed, i.e. overwritten while still pending. */
uint32_t acquisition_dropped_blocks(void);

/** Frames delivered by the DMA since start, including any that were dropped. */
uint32_t acquisition_frames_produced(void);

bool acquisition_is_running(void);

/** True once the ADC self-calibration has completed successfully. */
bool acquisition_calibrated(void);

#ifdef __cplusplus
}
#endif

#endif /* ACQUISITION_H */
