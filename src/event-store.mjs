import fs from 'node:fs';
import path from 'node:path';
import { ensureDir, newId, nowIso } from './core.mjs';

export class EventStore {
  constructor(file) {
    this.file = file;
    ensureDir(path.dirname(file));
    /** @type {any[]} */
    this.events = [];
    if (fs.existsSync(file)) {
      const lines = fs.readFileSync(file, 'utf8').split('\n').filter(Boolean);
      for (const line of lines) {
        this.events.push(JSON.parse(line));
      }
    }
  }

  append(type, payload = {}) {
    const event = {
      eventId: newId('evt'),
      seq: this.events.length + 1,
      ts: nowIso(),
      type,
      ...payload,
    };
    this.events.push(event);
    fs.appendFileSync(this.file, `${JSON.stringify(event)}\n`);
    return event;
  }
}
