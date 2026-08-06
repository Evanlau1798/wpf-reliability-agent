# WPF Reliability Agent

An evidence-driven reliability agent that turns WPF runtime failures into a bounded, approval-gated mitigation workflow.

## Problem

WPF binding, UI, exception, and rendering signals are fragmented. Traditional monitoring can report symptoms but does not safely correlate evidence, request targeted diagnostics, obtain human approval, execute a typed mitigation, and verify the result.

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
