// Socials/marketing API: credentials (file-backed, never echoed), draft
// generation, and the generate -> approve -> publish pipeline. Publish is
// only ever reachable from 'approved' -- there is no path from draft
// straight to posted, on purpose.

import express from "express";
import * as store from "./db/store.js";
import { saveCredentials, deleteCredentials, credentialsStatus } from "./socialsCredentials.js";
import { listTemplates, generateContent, publishToX, SocialsError } from "./socialsService.js";

export function socialsApiRouter(root) {
  const router = express.Router({ mergeParams: true });

  function getProjectOr404(req, res) {
    const project = store.getProject(root, req.params.projectId);
    if (!project) {
      res.status(404).json({ error: "Project not found." });
      return null;
    }
    return project;
  }

  router.get("/templates", (_req, res) => {
    res.json({ templates: listTemplates() });
  });

  router.get("/credentials", (req, res) => {
    if (!getProjectOr404(req, res)) return;
    res.json(credentialsStatus(root, req.params.projectId));
  });

  router.post("/credentials", (req, res) => {
    if (!getProjectOr404(req, res)) return;
    const { apiKey, apiSecret, accessToken, accessTokenSecret } = req.body || {};
    try {
      saveCredentials(root, req.params.projectId, { apiKey, apiSecret, accessToken, accessTokenSecret });
      res.json(credentialsStatus(root, req.params.projectId));
    } catch (err) {
      res.status(400).json({ error: err.message || String(err) });
    }
  });

  router.delete("/credentials", (req, res) => {
    if (!getProjectOr404(req, res)) return;
    deleteCredentials(root, req.params.projectId);
    res.json({ ok: true });
  });

  router.get("/posts", (req, res) => {
    if (!getProjectOr404(req, res)) return;
    res.json({ posts: store.listSocialPosts(root, req.params.projectId) });
  });

  router.post("/posts/generate", (req, res) => {
    const project = getProjectOr404(req, res);
    if (!project) return;
    const { template } = req.body || {};
    try {
      const content = generateContent(project, template || "custom");
      const postId = store.createSocialPost(root, { projectId: project.id, template, content });
      res.json({ ok: true, post: store.getSocialPost(root, postId) });
    } catch (err) {
      const status = err instanceof SocialsError ? 400 : 500;
      res.status(status).json({ error: err.message || String(err) });
    }
  });

  router.patch("/posts/:postId", (req, res) => {
    if (!getProjectOr404(req, res)) return;
    const post = store.getSocialPost(root, req.params.postId);
    if (!post || post.project_id !== req.params.projectId) return res.status(404).json({ error: "Post not found." });
    if (post.status === "published") return res.status(400).json({ error: "Can't edit a post that's already published." });
    const { content } = req.body || {};
    if (!content || !content.trim()) return res.status(400).json({ error: "content is required." });
    store.updateSocialPostContent(root, post.id, content);
    res.json({ ok: true, post: store.getSocialPost(root, post.id) });
  });

  router.post("/posts/:postId/approve", (req, res) => {
    if (!getProjectOr404(req, res)) return;
    const post = store.getSocialPost(root, req.params.postId);
    if (!post || post.project_id !== req.params.projectId) return res.status(404).json({ error: "Post not found." });
    if (post.status !== "draft") return res.status(400).json({ error: `Only a draft can be approved (this post is ${post.status}).` });
    store.setSocialPostStatus(root, post.id, "approved");
    res.json({ ok: true, post: store.getSocialPost(root, post.id) });
  });

  router.post("/posts/:postId/unapprove", (req, res) => {
    if (!getProjectOr404(req, res)) return;
    const post = store.getSocialPost(root, req.params.postId);
    if (!post || post.project_id !== req.params.projectId) return res.status(404).json({ error: "Post not found." });
    if (post.status !== "approved") return res.status(400).json({ error: "Only an approved (not yet published) post can be sent back to draft." });
    store.setSocialPostStatus(root, post.id, "draft");
    res.json({ ok: true, post: store.getSocialPost(root, post.id) });
  });

  router.post("/posts/:postId/publish", async (req, res) => {
    if (!getProjectOr404(req, res)) return;
    const post = store.getSocialPost(root, req.params.postId);
    if (!post || post.project_id !== req.params.projectId) return res.status(404).json({ error: "Post not found." });
    if (post.status !== "approved") {
      return res.status(400).json({ error: `Only an approved post can be published (this post is ${post.status}). Approve it first.` });
    }
    try {
      const result = await publishToX(root, req.params.projectId, { text: post.content });
      store.setSocialPostStatus(root, post.id, "published", result);
      res.json({ ok: true, post: store.getSocialPost(root, post.id) });
    } catch (err) {
      store.setSocialPostStatus(root, post.id, "approved", { error: err.message || String(err) });
      const status = err instanceof SocialsError ? 400 : 500;
      res.status(status).json({ error: err.message || String(err) });
    }
  });

  router.delete("/posts/:postId", (req, res) => {
    if (!getProjectOr404(req, res)) return;
    const post = store.getSocialPost(root, req.params.postId);
    if (!post || post.project_id !== req.params.projectId) return res.status(404).json({ error: "Post not found." });
    if (post.status === "published") return res.status(400).json({ error: "Published posts stay in the record — delete isn't offered for them." });
    store.deleteSocialPost(root, post.id);
    res.json({ ok: true });
  });

  return router;
}
