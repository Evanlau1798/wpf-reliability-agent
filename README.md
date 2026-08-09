# WPF Reliability Agent

An evidence-driven reliability agent that turns WPF binding, UI, and performance signals into a cloud incident where Gemini requests bounded diagnostics, a human approves the single typed mitigation, and before/after evidence verifies the result.

## Problem

WPF binding, UI, exception, and rendering signals are fragmented across trace output and runtime state that ordinary logs and APM metrics do not correlate into a WPF-specific diagnosis. Traditional monitoring can report symptoms but does not safely request targeted diagnostics, bind a proposed action to the exact evidence and human approval, execute the typed mitigation once, and verify the result.

## Architecture

The P0 design uses one in-process WPF sensor, HTTPS telemetry batches and command long-polling, one Python package deployed as two Cloud Run services, Pub/Sub, Firestore, Google ADK, and Gemini.

## Quickstart

Implementation and reproducible setup instructions will be added as each verified Gate is completed.

## Reuse

Selected WPF diagnostic concepts and primitives are adapted from `Evanlau1798/wpf-devtools-mcp` at pinned upstream source revision `900ac97cf9b69b4a3c1f4899b08c9b1e78212af3`. See `REUSE_DISCLOSURE.md` and `reuse-manifest.json`.

## Security

P0 exposes bounded read-only diagnostics and one typed mutation, `recovery.set_feature_flag`, which requires exact human approval. Arbitrary shell, file, process, injection, screenshot, OCR, and source-write capabilities are excluded.

## Demo

The primary scenario is an `ExperimentalPeopleGrid` binding storm with rendering degradation. A verified feature rollback is reported as `MITIGATED`, never `RESOLVED`.
