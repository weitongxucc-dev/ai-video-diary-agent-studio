# Beta P1 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the known beta P1 blockers before expanding internal testing.

**Architecture:** Keep the current FastAPI, Next.js and Electron split. Make the smallest targeted changes: dependency security first, truthful dashboard copy second, and one realistic end-to-end smoke test third.

**Tech Stack:** pnpm, Next.js App Router, TypeScript, FastAPI, pytest, Node-based contract checks.

---

## Task 1: Fix PostCSS Audit Finding

**Files:**
- Modify: `package.json`
- Modify: `pnpm-lock.yaml`

- [x] Confirm `corepack pnpm audit --prod` reports vulnerable `postcss@8.4.31`.
- [x] Apply the smallest dependency fix that resolves PostCSS to `>=8.5.10`.
- [x] Run `corepack pnpm audit --prod`.
- [x] Run `corepack pnpm --dir apps/web list next postcss --depth 4`.

## Task 2: Remove Hardcoded Home Metric

**Files:**
- Modify: `apps/web/scripts/workflow-contract-check.mjs`
- Modify: `apps/web/src/app/page.tsx`

- [x] Add a contract check that rejects `今日新增 12 条`.
- [x] Replace the hardcoded metric with real source counts and an offline-safe detail.
- [x] Run `corepack pnpm test:web-contract`.

## Task 3: Add API Happy-Path Smoke Test Script

**Files:**
- Create: `apps/api/scripts/beta_happy_path_smoke.py`
- Modify: `package.json`

- [x] Add a script that starts from a live API base URL, fetches today's Episode, generates topics, approves the first topic, and verifies script readiness.
- [x] Add `test:e2e-smoke` to root scripts.
- [x] Verify the script against a dev API or document why it cannot run locally.

## Verification Batch

Run:

```bash
corepack pnpm audit --prod
corepack pnpm test:web-contract
corepack pnpm typecheck:web
corepack pnpm lint:web
corepack pnpm test:api
```
