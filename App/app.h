/**
 * @file    app.h
 * @brief   Application entry points, so main.c stays the CubeMX template.
 *
 * The whole of main() is:
 *
 *     MX_xxx_Init();
 *     App_Init();
 *     while (1) { App_Loop(); }
 *
 * which keeps the generated file untouched and makes the application reviewable
 * as its own unit.
 */
#ifndef APP_H
#define APP_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Initialise every module and start the acquisition chain. */
void App_Init(void);

/** One iteration of the cooperative super-loop: service, process, render, send. */
void App_Loop(void);

/** Recording state, toggled by KEY_OK on the ECG page or by the PC. */
void App_SetRecording(bool on);
bool App_Recording(void);

/**
 * Flip recording and the PC stream together, and nothing else.
 *
 * Called only from KK_UI's custom-page input callback, which is the one place
 * that knows the ECG page currently has focus. An earlier version asked the
 * application's own button queue and a mirrored page id instead; see
 * docs/PHASE1_REVIEW_FIX_HANDOFF.md.
 */
void App_ToggleRecording(void);

/** Seconds since the current recording started. */
uint32_t App_SessionSeconds(void);

#ifdef __cplusplus
}
#endif

#endif /* APP_H */
