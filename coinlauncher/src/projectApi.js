// API for the new project/state-machine layer. Launch (mint) itself still
// happens via POST /api/launch in server.js -- this router covers
// everything that reads or acts on a project after it exists: state,
// wallets, the audit trail, and the hardened distribution action.

import express from "express";
import * as store from "./db/store.js";
import { refreshState, STATES, verifyAndRecordPool } from "./stateMachine.js";
import { runDistribution, DistributionError } from "./distributionService.js";
import { runFundingSwap, runFundingForward, FundingSwapError } from "./fundingSwapService.js";

export function projectApiRouter(root) {
  const router = express.Router();

  function getProjectOr404(req, res) {
    const project = store.getProject(root, req.params.projectId);
    if (!project) {
      res.status(404).json({ error: "Project not found." });
      return null;
    }
    return project;
  }

  router.get("/", (_req, res) => {
    const projects = store.listProjects(root).map((p) => {
      const wallets = store.getWalletsByProject(root, p.id);
      return { ...p, walletCount: wallets.length };
    });
    res.json({ projects });
  });

  router.get("/:projectId", (req, res) => {
    const project = getProjectOr404(req, res);
    if (!project) return;
    const wallets = store.getWalletsByProject(root, project.id);
    const state = store.getState(root, project.id);
    res.json({ project, wallets, state });
  });

  router.get("/:projectId/state", async (req, res) => {
    const project = getProjectOr404(req, res);
    if (!project) return;
    try {
      const result = await refreshState(root, project.id);
      res.json({ states: STATES, ...result });
    } catch (err) {
      res.status(500).json({ error: err.message || String(err) });
    }
  });

  router.get("/:projectId/transactions", (req, res) => {
    const project = getProjectOr404(req, res);
    if (!project) return;
    const transactions = store.getTransactionsByProject(root, project.id, Number(req.query.limit) || 100);
    res.json({ transactions });
  });

  router.get("/:projectId/history", (req, res) => {
    const project = getProjectOr404(req, res);
    if (!project) return;
    res.json({ history: store.getStateHistory(root, project.id) });
  });

  router.post("/:projectId/distribute", async (req, res) => {
    const project = getProjectOr404(req, res);
    if (!project) return;
    const { allocations, force } = req.body || {};
    try {
      const result = await runDistribution(root, project.id, { allocations, force: !!force });
      res.json({ ok: true, ...result });
    } catch (err) {
      const status = err instanceof DistributionError ? 400 : 500;
      res.status(status).json({ error: err.message || String(err) });
    }
  });

  router.post("/:projectId/liquidity/verify-pool", async (req, res) => {
    const project = getProjectOr404(req, res);
    if (!project) return;
    const { poolAddress } = req.body || {};
    if (!poolAddress) return res.status(400).json({ error: "poolAddress is required." });
    try {
      const result = await verifyAndRecordPool(root, project.id, poolAddress);
      res.json(result);
    } catch (err) {
      res.status(500).json({ error: err.message || String(err) });
    }
  });

  router.post("/:projectId/liquidity/config", (req, res) => {
    const project = getProjectOr404(req, res);
    if (!project) return;
    const { slippageBps, reserveSol, swapPercent } = req.body || {};
    try {
      store.updateLiquidityConfig(root, project.id, { slippageBps, reserveSol, swapPercent });
      res.json({ ok: true, project: store.getProject(root, project.id) });
    } catch (err) {
      res.status(500).json({ error: err.message || String(err) });
    }
  });

  router.post("/:projectId/funding-swap", async (req, res) => {
    const project = getProjectOr404(req, res);
    if (!project) return;
    const { swapPercent, slippageBps, force } = req.body || {};
    try {
      const result = await runFundingSwap(root, project.id, { swapPercent, slippageBps, force: !!force });
      res.json({ ok: true, ...result });
    } catch (err) {
      const status = err instanceof FundingSwapError ? 400 : 500;
      res.status(status).json({ error: err.message || String(err) });
    }
  });

  router.post("/:projectId/funding-forward", async (req, res) => {
    const project = getProjectOr404(req, res);
    if (!project) return;
    const { force } = req.body || {};
    try {
      const result = await runFundingForward(root, project.id, { force: !!force });
      res.json({ ok: true, ...result });
    } catch (err) {
      const status = err instanceof FundingSwapError ? 400 : 500;
      res.status(status).json({ error: err.message || String(err) });
    }
  });

  return router;
}
