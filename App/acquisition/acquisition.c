#include "acquisition.h"

#include "stm32f1xx_hal.h"

#include "adc.h"
#include "tim.h"

/*
 * A single circular DMA buffer, split into ACQ_BLOCK_COUNT blocks, with the
 * half-transfer and transfer-complete interrupts marking each half as ready.
 *
 * STM32F1 DMA has no peripheral double-buffer mode, so "double buffering" here
 * means the two halves of one circular buffer. That is why the block size and
 * the total length must both be even multiples of the channel count: the DMA is
 * free-running and will happily overwrite a half the consumer has not read, so
 * readiness is tracked in a mask and an overrun is counted rather than hidden.
 */
static uint16_t s_buffer[ADC_DMA_WORD_COUNT];

static volatile uint8_t  s_ready_mask;
static volatile uint32_t s_block_first[ACQ_BLOCK_COUNT];
static volatile uint32_t s_frames_produced;
static volatile uint32_t s_dropped;
static volatile bool     s_overrun_seen;
static bool              s_running;
static bool              s_calibrated;

void acquisition_init(void)
{
    s_ready_mask = 0U;
    s_frames_produced = 0U;
    s_dropped = 0U;
    s_overrun_seen = false;
    s_running = false;
    s_calibrated = false;
    for (uint8_t i = 0U; i < ACQ_BLOCK_COUNT; i++) {
        s_block_first[i] = 0U;
    }
}

bool acquisition_start(void)
{
    /* Prerequisite documented in stm32f1xx_hal_adc_ex.c: the ADC must be
     * disabled for calibration, which is why this runs before any Start call.
     * The HAL leaves the ADC enabled on completion. */
    if (HAL_ADCEx_Calibration_Start(&hadc1) != HAL_OK) {
        return false;
    }
    s_calibrated = true;

    if (HAL_ADC_Start_DMA(&hadc1, s_buffer, ADC_DMA_WORD_COUNT) != HAL_OK) {
        return false;
    }

    /* Only now does the 1 kHz trigger exist, so no conversion can be missed. */
    if (HAL_TIM_Base_Start(&htim3) != HAL_OK) {
        (void)HAL_ADC_Stop_DMA(&hadc1);
        return false;
    }

    s_running = true;
    return true;
}

/* ------------------------------------------------------------- ISR context */

/* Called with the DMA half-transfer interrupt: block 0 is now complete. */
void HAL_ADC_ConvHalfCpltCallback(ADC_HandleTypeDef *hadc)
{
    if (hadc != &hadc1) {
        return;
    }
    if ((s_ready_mask & 0x01U) != 0U) {
        s_dropped++;
        s_overrun_seen = true;
    }
    s_block_first[0] = s_frames_produced;
    s_frames_produced += ACQ_FRAMES_PER_BLOCK;
    s_ready_mask |= 0x01U;
}

/* Called with the DMA full-transfer interrupt: block 1 is now complete. */
void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef *hadc)
{
    if (hadc != &hadc1) {
        return;
    }
    if ((s_ready_mask & 0x02U) != 0U) {
        s_dropped++;
        s_overrun_seen = true;
    }
    s_block_first[1] = s_frames_produced;
    s_frames_produced += ACQ_FRAMES_PER_BLOCK;
    s_ready_mask |= 0x02U;
}

void HAL_ADC_ErrorCallback(ADC_HandleTypeDef *hadc)
{
    if (hadc == &hadc1) {
        s_running = false;
        s_overrun_seen = true;
    }
}

/* ---------------------------------------------------------- thread context */

bool acquisition_take_block(const uint16_t **words, uint16_t *frames,
                            uint32_t *first_frame_index)
{
    uint8_t bit;
    uint32_t primask;

    if (words == NULL || frames == NULL || first_frame_index == NULL) {
        return false;
    }

    /* Consume oldest-first so the consumer's view of the timeline is ordered. */
    primask = __get_PRIMASK();
    __disable_irq();
    if ((s_ready_mask & 0x01U) != 0U) {
        bit = 0U;
        s_ready_mask &= (uint8_t)~0x01U;
    } else if ((s_ready_mask & 0x02U) != 0U) {
        bit = 1U;
        s_ready_mask &= (uint8_t)~0x02U;
    } else {
        __set_PRIMASK(primask);
        return false;
    }
    *first_frame_index = s_block_first[bit];
    __set_PRIMASK(primask);

    *words = &s_buffer[bit * (ACQ_FRAMES_PER_BLOCK * 2U)];
    *frames = ACQ_FRAMES_PER_BLOCK;
    return true;
}

uint32_t acquisition_dropped_blocks(void)
{
    return s_dropped;
}

uint32_t acquisition_frames_produced(void)
{
    return s_frames_produced;
}

bool acquisition_is_running(void)
{
    return s_running;
}

bool acquisition_calibrated(void)
{
    return s_calibrated;
}
