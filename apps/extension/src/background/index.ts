/**
 * Service-worker entry point: wires the worker's logic to Chrome.
 */

import { apiRequest } from "../api/client";
import { submitJob } from "../api/jobs";
import { getSession } from "../api/session";
import { WEB_URL } from "../config";
import { API } from "../shared/messages";
import { createBackground, type SessionStore } from "./service";

const sessionStore: SessionStore = {
  async get(key) {
    return (await chrome.storage.session.get(key))[key];
  },
  set(key, value) {
    return chrome.storage.session.set({ [key]: value });
  },
  remove(key) {
    return chrome.storage.session.remove(key);
  },
};

const background = createBackground({
  submit: (payload, sessionTag) => submitJob(payload, { webUrl: WEB_URL, sessionTag }),
  session: () => getSession({ webUrl: WEB_URL }),
  request: (prepared, sessionTag) => apiRequest(prepared, { webUrl: WEB_URL, sessionTag }),
  store: sessionStore,
  extensionId: chrome.runtime.id,
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const answer = background.handle(message, sender);
  if (answer === null) return false;
  answer.then(sendResponse, () =>
    sendResponse(
      (message as { type?: unknown }).type === API
        ? { ok: false, kind: "network", status: null, code: null, message: "The request failed inside the extension." }
        : { kind: "unreachable", code: "network", message: "The save failed inside the extension." },
    ),
  );
  // Keeps the channel open for the asynchronous answer.
  return true;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  void background.forgetTab(tabId);
});
