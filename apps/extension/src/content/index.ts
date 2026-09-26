/**
 * Content-script entry point, injected into supported job-site pages only.
 *
 * Passive by design. It does nothing when the page loads; it reads the page
 * when the popup asks, answers, and that is all. It never submits a form,
 * never clicks, never talks to the network, and never asks for a save. The
 * one thing it writes is a value the person approved in the popup, into an
 * empty text field of the application form (./handler.ts, src/forms/fill.ts).
 */

import { answerContentMessage } from "./handler";

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id) return false;
  const answer = answerContentMessage(message, document, location.href);
  if (answer === undefined) return false;
  sendResponse(answer);
  return false;
});
