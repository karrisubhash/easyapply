/**
 * Popup entry point.
 */

import { WEB_URL } from "../config";
import { startPopup } from "./app";
import { chromePort } from "./port";

const root = document.getElementById("app");
if (root) {
  startPopup({ root, port: chromePort(location.search), webUrl: WEB_URL, extensionId: chrome.runtime.id });
}
