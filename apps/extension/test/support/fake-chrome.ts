/**
 * The parts of the Chrome extension APIs this extension uses, behaving the way
 * Chrome does where the behaviour matters:
 *
 *   - `runtime.sendMessage` from the popup reaches the background's listeners,
 *     with the popup page as sender; a listener that returns true answers
 *     later through `sendResponse`; nobody answering rejects, as Chrome's
 *     "message port closed" does.
 *   - `tabs.sendMessage` to a tab with no content script rejects with Chrome's
 *     own "Receiving end does not exist".
 *   - `tabs.query`/`tabs.get` show a tab's URL only for sites the extension has
 *     host permission for, as Chrome does without the "tabs" permission.
 *   - `storage.session` is an in-memory key/value store.
 *
 * A tab's content script is `answerContentMessage` over that tab's document —
 * exactly what src/content/index.ts runs (test/content.test.ts covers that
 * file itself): the posting, the application form, and filling it.
 */

import { answerContentMessage } from "../../src/content/handler";

export const EXTENSION_ID = "jjhciekcmhmngciccfgignmmlgjknakk";

type Listener = (message: unknown, sender: chrome.runtime.MessageSender, sendResponse: (value: unknown) => void) => unknown;

export interface FakeTab {
  id: number;
  url: string;
  /** The page, when a content script is running in it. */
  document?: Document;
}

const PERMITTED = [
  /^https:\/\/job-boards\.greenhouse\.io\//,
  /^https:\/\/job-boards\.eu\.greenhouse\.io\//,
  /^https:\/\/jobs\.lever\.co\//,
  /^http:\/\/localhost:3000\//,
];

export function createFakeChrome(tabs: FakeTab[], { activeTabId = tabs[0]?.id } = {}) {
  const listeners: Listener[] = [];
  const removedListeners: ((tabId: number) => void)[] = [];
  const session = new Map<string, unknown>();
  const popupSender = {
    id: EXTENSION_ID,
    url: `chrome-extension://${EXTENSION_ID}/popup.html`,
    origin: `chrome-extension://${EXTENSION_ID}`,
  };

  function visible(tab: FakeTab) {
    return { id: tab.id, url: PERMITTED.some((pattern) => pattern.test(tab.url)) ? tab.url : undefined };
  }

  const api = {
    runtime: {
      id: EXTENSION_ID,
      onMessage: {
        addListener(listener: Listener) {
          listeners.push(listener);
        },
      },
      sendMessage(message: unknown, sender: chrome.runtime.MessageSender = popupSender): Promise<unknown> {
        return new Promise((resolve, reject) => {
          let answered = false;
          let waiting = false;
          const sendResponse = (value: unknown) => {
            if (answered) return;
            answered = true;
            resolve(value);
          };
          for (const listener of listeners) {
            if (listener(message, sender, sendResponse) === true) waiting = true;
          }
          if (!answered && !waiting) {
            reject(new Error("The message port closed before a response was received."));
          }
        });
      },
    },
    tabs: {
      async query(query: { active?: boolean; currentWindow?: boolean }) {
        return tabs.filter((tab) => !query.active || tab.id === activeTabId).map(visible);
      },
      async get(tabId: number) {
        const tab = tabs.find((candidate) => candidate.id === tabId);
        if (!tab) throw new Error(`No tab with id: ${tabId}.`);
        return visible(tab);
      },
      async sendMessage(tabId: number, message: unknown) {
        const tab = tabs.find((candidate) => candidate.id === tabId);
        if (!tab?.document) {
          throw new Error("Could not establish connection. Receiving end does not exist.");
        }
        return answerContentMessage(message, tab.document, tab.url);
      },
      onRemoved: {
        addListener(listener: (tabId: number) => void) {
          removedListeners.push(listener);
        },
      },
    },
    storage: {
      session: {
        async get(key: string) {
          return session.has(key) ? { [key]: session.get(key) } : {};
        },
        async set(items: Record<string, unknown>) {
          for (const [key, value] of Object.entries(items)) session.set(key, structuredClone(value));
        },
        async remove(key: string) {
          session.delete(key);
        },
      },
    },
  };

  return {
    chrome: api,
    session,
    closeTab(tabId: number) {
      removedListeners.forEach((listener) => listener(tabId));
    },
  };
}
