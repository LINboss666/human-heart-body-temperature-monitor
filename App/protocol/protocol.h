/**
 * @file    protocol.h
 * @brief   USART1 binary link between the monitor and the PC tool. CONTRACT.
 *
 * This header is the single definition of the wire format. pc_monitor/protocol.py
 * mirrors it byte for byte, and tests/test_protocol_vectors.py fails if the two
 * drift. Change one side only together with the other, and bump PROTOCOL_VERSION.
 *
 * Frame, all multi-byte fields little-endian:
 *
 *   offset  size  field
 *   0       1     magic0   0xA5
 *   1       1     magic1   0x5A
 *   2       1     version  PROTOCOL_VERSION
 *   3       1     type     pkt_type_t
 *   4       2     sequence number
 *   6       2     payload length N
 *   8       4     device timestamp, milliseconds since boot (HAL_GetTick)
 *   12      N     payload
 *   12+N    2     CRC16-CCITT/FALSE over bytes 0 .. 11+N
 *
 * Total frame size is 14 + N. There is no in-band escape: the receiver resyncs
 * by scanning for the magic pair after any error, so payload bytes must never be
 * assumed to be quoted. That is safe because N is validated before the CRC is
 * located.
 *
 * Bandwidth at the default configuration (docs/PROTOCOL.md derives this in full):
 * 50 ECG_BATCH/s at 69 bytes, 2 STATUS/s at 57 bytes and 2 TEMP_STATUS/s at 22
 * bytes is 3608 byte/s against a 23040 byte/s line, i.e. 15.7 % utilisation.
 */
#ifndef PROTOCOL_H
#define PROTOCOL_H

#include <stdbool.h>
#include <stdint.h>

#include "app_config.h"

#ifdef __cplusplus
extern "C" {
#endif

/* --------------------------------------------------------------- constants */

#define PKT_MAGIC0             0xA5U
#define PKT_MAGIC1             0x5AU
#define PKT_HEADER_SIZE        PROTOCOL_HEADER_SIZE   /* 12 */
#define PKT_CRC_SIZE           PROTOCOL_CRC_SIZE      /* 2  */
#define PKT_OVERHEAD           (PKT_HEADER_SIZE + PKT_CRC_SIZE)  /* 14 */
#define PKT_MAX_PAYLOAD        PROTOCOL_MAX_PAYLOAD
#define PKT_MAX_FRAME          PROTOCOL_MAX_FRAME

/* ------------------------------------------------------------------- types */

typedef enum {
    /* device -> host */
    PKT_HELLO        = 0x01U, /**< emitted at boot and on GET_RTC-less reconnect */
    PKT_STATUS       = 0x02U, /**< diagnostics dump, see diag payload layout */
    PKT_ECG_BATCH    = 0x10U, /**< the 1 kHz record, batched */
    PKT_TEMP_STATUS  = 0x11U, /**< temperature view at its own cadence */
    PKT_RTC_RESPONSE = 0x20U, /**< answer to GET_RTC / confirmation of SET_RTC */
    PKT_PONG         = 0x31U,
    PKT_ACK          = 0x40U,
    PKT_NACK         = 0x41U,
    /* host -> device */
    PKT_START_STREAM = 0x80U,
    PKT_STOP_STREAM  = 0x81U,
    PKT_SET_RTC      = 0x82U,
    PKT_GET_RTC      = 0x83U,
    PKT_SET_CONFIG   = 0x84U, /**< notch / alarm thresholds / temp alarms */
    PKT_PING         = 0x90U
} pkt_type_t;

/* ---------------------------------------------------------- ECG_BATCH body */

#define ECG_BATCH_MAX_SAMPLES       20U

/* Payload offsets, with n = sample_count. */
#define ECGP_COUNT              0U   /* u8   */
#define ECGP_PERIOD_US          1U   /* u16  = ADC_SAMPLE_PERIOD_US */
#define ECGP_FIRST_INDEX        3U   /* u32  index of the first ECG sample in this batch */
#define ECGP_SAMPLES            7U   /* u16[n] raw ADC codes */
#define ECGP_SIZE(n)            (ECGP_SAMPLES + (n) * 2U)

/* Fixed tail that follows the samples; total payload = ECGP_SIZE(n) + ECGP_TAIL.
 * ECGP_TAIL is derived from the widest tail field instead of being hand-counted:
 * the literal 7 it used to carry matched the four u8/u16 fields below but not the
 * trailing u16, so ECGT_FLAGS was written two bytes deep and sent one byte wide.
 * CRC still passed, so nothing complained and status_flags_t bits 8..15 simply
 * never reached the host. */
#define ECGT_TEMP_RAW           0U   /* u16  most recent temperature code */
#define ECGT_TEMP_CENTI         2U   /* i16  only meaningful if flags say so */
#define ECGT_HR_BPM             4U   /* u8   0 when invalid */
#define ECGT_HR_STATE           5U   /* u8   hr_state_t */
#define ECGT_FLAGS              6U   /* u16  status_flags_t */
#define ECGP_TAIL               (ECGT_FLAGS + 2U)

/* --------------------------------------------------------- TEMP_STATUS body */

#define TEMPP_RAW               0U   /* u16 */
#define TEMPP_MV                2U   /* u16 pin millivolts */
#define TEMPP_CENTI             4U   /* i16 */
#define TEMPP_STATE             6U   /* u8  temp_state_t */
#define TEMPP_SIZE              8U   /* 7 used, padded to even */

/* ------------------------------------------------------------- STATUS body */

#define STP_ADC_RUNNING         0U   /* u8  */
#define STP_DMA_BLOCKS          1U   /* u32 */
#define STP_DMA_DROPPED         5U   /* u32 */
#define STP_ECG_SAMPLES         9U   /* u32 */
#define STP_TEMP_VALID          13U  /* u8  */
#define STP_TEMP_CENTI          14U  /* i16 */
#define STP_HR_BPM              16U  /* u8  */
#define STP_HR_STATE            17U  /* u8  */
#define STP_OLED_PRESENT        18U  /* u8  */
#define STP_OLED_ADDR           19U  /* u8  7-bit address, 0 if none */
#define STP_RTC_VALID           20U  /* u8  */
#define STP_UART_TX             21U  /* u32 */
#define STP_UART_RX             25U  /* u32 */
#define STP_UART_CRC_ERR        29U  /* u32 */
#define STP_PROTO_ERR           33U  /* u32 */
#define STP_FLAGS               37U  /* u16 */
#define STP_UPTIME_S            39U  /* u32 */
#define STP_SIZE                43U

/* -------------------------------------------------------------- HELLO body */

#define HELPP_FW_MAJOR          0U
#define HELPP_FW_MINOR          1U
#define HELPP_FW_PATCH          2U
#define HELPP_PROTO_VER         3U
#define HELPP_SAMPLE_RATE       4U   /* u16 */
#define HELPP_BATCH_MAX         6U   /* u8  */
#define HELPP_ADC_BITS          7U   /* u8  */
#define HELPP_CAPS              8U   /* u16 capability bitmask */
#define HELPP_SIZE              12U

#define CAP_TEMP_CALIBRATED     (1U << 0)
#define CAP_OLED_PRESENT        (1U << 1)
#define CAP_LEAD_HW_DETECT      (1U << 2) /**< 0 = no lead-off hardware, see lead_state */
#define CAP_PROBE_HW_DETECT     (1U << 3)
#define CAP_FRONTEND_VERIFIED   (1U << 4)
#define CAP_RTC_BATTERY_BACKED  (1U << 5) /**< 0 = VBAT retention unproven */

/* ----------------------------------------------------------- RTC payloads */

#define RTCP_YEAR               0U   /* u16 */
#define RTCP_MONTH              2U   /* u8  */
#define RTCP_DAY                3U   /* u8  */
#define RTCP_HOUR               4U   /* u8  */
#define RTCP_MINUTE             5U   /* u8  */
#define RTCP_SECOND             6U   /* u8  */
#define RTCP_CAL_SIZE           7U
#define RTC_RESPONSE_EXTRA      4U   /* trailing u32 epoch, so 11 bytes total */

/* ------------------------------------------------------- START/STOP/CONFIG */

#define CFGP_NOTCH              0U   /* u8 ecg_notch_t */
#define CFGP_HR_LOW             1U   /* u8 */
#define CFGP_HR_HIGH            2U   /* u8 */
#define CFGP_TEMP_LOW           3U   /* i16 centi */
#define CFGP_TEMP_HIGH          5U   /* i16 centi */
#define CFGP_SIZE               7U

/* -------------------------------------------------------- ACK / NACK body */

#define ACKP_TYPE               0U   /* u8  packet type being answered */
#define ACKP_SEQ                1U   /* u16 sequence of the packet being answered */
#define NACKP_REASON            3U   /* u8  nack_reason_t */
#define ACKP_SIZE               3U
#define NACKP_SIZE              4U

typedef enum {
    NACK_NONE = 0,
    NACK_BAD_CRC,
    NACK_UNSUPPORTED_TYPE,
    NACK_MALFORMED_LENGTH,
    NACK_BAD_VALUE,          /**< e.g. an impossible calendar time */
    NACK_BUSY
} nack_reason_t;

/* ------------------------------------------------------------ status flags */

typedef enum {
    LEAD_UNKNOWN = 0,        /**< no lead-off hardware configured: the default */
    LEAD_CONNECTED = 1,      /**< only asserted by real hardware detection */
    LEAD_DISCONNECTED = 2,   /**< only asserted by real hardware detection */
    LEAD_SIGNAL_POOR = 3     /**< software judgement, NOT an electrode verdict */
} lead_state_t;

typedef enum {
    HR_INVALID = 0,
    HR_ACQUIRING = 1,
    HR_NORMAL = 2,
    HR_LOW = 3,
    HR_HIGH = 4
} hr_state_t;

typedef enum {
    TEMP_OK = 0,
    TEMP_LOW = 1,
    TEMP_HIGH = 2,
    TEMP_PROBE_FAULT = 3,
    TEMP_UNCALIBRATED = 4
} temp_state_t;

#define SFLAG_LEAD_SHIFT        0U
#define SFLAG_LEAD_MASK         (0x7U << SFLAG_LEAD_SHIFT)
#define SFLAG_TEMP_SHIFT        3U
#define SFLAG_TEMP_MASK         (0x7U << SFLAG_TEMP_SHIFT)
#define SFLAG_HR_VALID          (1U << 6)
#define SFLAG_RECORDING         (1U << 7)
#define SFLAG_OLED              (1U << 8)
#define SFLAG_ADC_RUNNING       (1U << 9)
#define SFLAG_RTC_VALID         (1U << 10)
#define SFLAG_DMA_DROPPED       (1U << 11)
#define SFLAG_NOTCH_SHIFT       12U
#define SFLAG_NOTCH_MASK        (0x3U << SFLAG_NOTCH_SHIFT)
#define SFLAG_TEMP_UNCALIB      (1U << 14)

#define SFLAG_SET(v, shift, mask, val) \
    ((uint16_t)(((v) & ~(mask)) | ((((uint16_t)(val) << (shift)) & (mask)))))

/* --------------------------------------------------------------- receive API */

typedef struct {
    uint8_t  version;
    uint8_t  type;
    uint16_t sequence;
    uint16_t length;
    uint32_t device_ts_ms;
    const uint8_t *payload;   /**< points into the caller's buffer */
} pkt_frame_t;

typedef enum {
    PKT_NEED_MORE = 0,   /**< consume the bytes reported in consumed */
    PKT_OK       = 1,
    PKT_ERR_CRC  = -1,
    PKT_ERR_VERSION = -2,
    PKT_ERR_LENGTH  = -3
} pkt_result_t;

/**
 * Scan a byte stream for one complete frame.
 *
 * Resynchronisation is internal: bytes before the magic pair, and a bad frame's
 * leading magic, are discarded so a corrupted stream does not deadlock. Returns
 * PKT_OK with *consumed set past the CRC; PKT_NEED_MORE means the caller should
 * append more bytes and call again with the same buffer.
 */
pkt_result_t pkt_parse(const uint8_t *buf, uint16_t len,
                       pkt_frame_t *out, uint16_t *consumed);

/* --------------------------------------------------------------- transmit API */

/** Build a frame into dst. Returns total length, or 0 if dst_len is too small. */
uint16_t pkt_build(uint8_t *dst, uint16_t dst_len, uint8_t type,
                   uint16_t sequence, uint32_t device_ts_ms,
                   const uint8_t *payload, uint16_t payload_len);

/** Little-endian field writers used by both sides of the link. */
void pkt_put_u16(uint8_t *p, uint16_t v);
void pkt_put_u32(uint8_t *p, uint32_t v);
void pkt_put_i16(uint8_t *p, int16_t v);
uint16_t pkt_get_u16(const uint8_t *p);
uint32_t pkt_get_u32(const uint8_t *p);
int16_t  pkt_get_i16(const uint8_t *p);

#ifdef __cplusplus
}
#endif

#endif /* PROTOCOL_H */
