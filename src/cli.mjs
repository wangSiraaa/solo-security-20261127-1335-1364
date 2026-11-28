#!/usr/bin/env node
// 离线查册器 / 寄送台命令行。全部操作本地完成，不发起任何网络请求。
//
// 用法：
//   node src/cli.mjs init-ledger  --vault A --file data/A.json [--name 照片处 --materials photo]
//   node src/cli.mjs append       --file data/A.json --name 报价处 --materials quote --op rotate
//   node src/cli.mjs revoke       --file data/A.json --name 备用处
//   node src/cli.mjs fork         --file A.json --other B.json
//   node src/cli.mjs view         --file data/A.json
//   node src/cli.mjs route        --file data/A.json --name 报价处 --material quote
//   node src/cli.mjs seal         --file data/A.json --name 照片处 --material photo --body '{"photoId":"P1"}' --out data/p1.json
//   node src/cli.mjs open         --parcel data/p1.json --key <enc-pkcs8-base64-file>
import fs from 'node:fs';
import path from 'node:path';
import {
  Ledger,
  Seat,
  MailDesk,
  rosterEntry,
  revokeEntry,
  openSeal,
  pageBody,
  pageId,
  verifySignedJson,
  generateSigningKeyPair,
  publicFromPrivate,
} from './index.mjs';

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) out[a.slice(2)] = argv[++i];
    else out._.push(a);
  }
  return out;
}

function loadStore(file) {
  const raw = JSON.parse(fs.readFileSync(file, 'utf8'));
  const ledger = new Ledger(raw.vaultId, raw.vaultSignPrivate, raw.pages);
  ledger.vaultKeys = new Map(Object.entries(raw.vaultKeys ?? {}));
  ledger.witnessKeys = new Map(Object.entries(raw.witnessKeys ?? {}));
  return { raw, ledger };
}

function saveStore(file, raw, ledger) {
  raw.pages = ledger.pages;
  fs.mkdirSync(path.dirname(path.resolve(file)), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(raw, null, 2));
}

function cmdInitLedger(args) {
  const vaultSign = generateSigningKeyPair(); // 分库自己的签名钥，与收件席钥分开
  const ledger = new Ledger(args.vault, vaultSign.privatePkcs8);
  const raw = {
    vaultId: args.vault,
    vaultSignPrivate: vaultSign.privatePkcs8,
    vaultKeys: {},
    witnessKeys: {},
    pages: [],
    seats: [],
  };
  if (args.name) {
    const seat = new Seat(`本地席-${args.name}`);
    seat.generateKey(0);
    const kinds = (args.materials ?? 'photo,quote').split(',');
    ledger.append([rosterEntry('add', args.name, seat, { materialsKinds: kinds })]);
    raw.seats.push({ seatId: seat.seatId, keys: [...seat.keys.values()] });
  }
  saveStore(args.file, raw, ledger);
  console.log(`已建名册：${args.file}（分库 ${args.vault}，${ledger.pages.length} 页）`);
}

function cmdAppend(args, op = 'rotate') {
  const { raw, ledger } = loadStore(args.file);
  const seatRec = raw.seats?.find((s) => s.seatId === args.seat) ?? raw.seats?.[0];
  if (!seatRec) throw new Error('本地无收件席钥匙材料，无法生成条目');
  const seat = new Seat(seatRec.seatId);
  for (const [gen, k] of seatRec.keys.entries()) seat.keys.set(gen, k);
  if (op === 'rotate') seat.rotateKey();
  const kinds = (args.materials ?? 'photo,quote').split(',');
  const page = ledger.append([rosterEntry(op, args.name, seat, { materialsKinds: kinds })]);
  seatRec.keys = [...seat.keys.values()];
  saveStore(args.file, raw, ledger);
  console.log(`已续写页 ${pageId(page).slice(0, 12)}（seq=${page.seq}）`);
}

function cmdRevoke(args) {
  const { raw, ledger } = loadStore(args.file);
  const seatRec = raw.seats?.find((s) => s.seatId === args.seat) ?? raw.seats?.[0];
  const seat = new Seat(seatRec.seatId);
  for (const [gen, k] of seatRec.keys.entries()) seat.keys.set(gen, k);
  const page = ledger.append([revokeEntry(args.name, seat)]);
  saveStore(args.file, raw, ledger);
  console.log(`已撤销 ${args.name}，页 ${pageId(page).slice(0, 12)}`);
}

function cmdFork(args) {
  const aStore = loadStore(args.file);
  const a = aStore.ledger;
  const bStore = loadStore(args.other);
  const b = bStore.ledger;
  // 离线交换分库公钥：从对方存档的分库私钥反推公钥并登记，供独立验签
  const bVaultPub = publicFromPrivate(bStore.raw.vaultSignPrivate);
  const aVaultPub = publicFromPrivate(aStore.raw.vaultSignPrivate);
  a.vaultKeys.set(b.vaultId, bVaultPub);
  b.vaultKeys.set(a.vaultId, aVaultPub);
  aStore.raw.vaultKeys = Object.fromEntries(a.vaultKeys);
  bStore.raw.vaultKeys = Object.fromEntries(b.vaultKeys);
  // 离线收存对方链页（验签失败会抛错）
  for (const p of b.chainPages()) {
    if (!a.lookupPage(pageId(p))) a.ingestLoose(p);
  }
  saveStore(args.file, aStore.raw, a);
  const report = a.reconcile(b);
  if (report.kind === 'fork') {
    console.log(`分歧：共有最后一页 ${report.commonBaseId.slice(0, 12)}`);
    for (const p of report.mineOnly) console.log(`  本库独有 ${pageId(p).slice(0, 12)} seq=${p.seq} 作者=${p.authorVault}`);
    for (const p of report.theirsOnly) {
      const key = b.vaultKeys.get(p.authorVault);
      const ok = key ? verifySignedJson(key, pageBody(p), p.signature) : '（缺对方公钥）';
      console.log(`  对方独有 ${pageId(p).slice(0, 12)} seq=${p.seq} 作者=${p.authorVault} 验真=${ok}`);
    }
  } else {
    console.log(`对账结果：${report.kind}`);
  }
}

function cmdView(args) {
  const { ledger } = loadStore(args.file);
  const view = ledger.view();
  for (const name of view.names()) {
    const rec = view.lookup(name);
    const kinds = rec.lastEntry.materialsKinds?.join('/') ?? '—';
    const conflict = view.conflictedNames.has(name) ? ' [冲突未决]' : '';
    console.log(`${name}\t${rec.status}\t材料=${kinds}${conflict}`);
  }
}

function cmdRoute(args) {
  const { ledger } = loadStore(args.file);
  const d = ledger.view().routingDecision(args.name, args.material);
  console.log(`${d.decision.toUpperCase()}\t${d.name}\t${d.reason}`);
  process.exitCode = d.decision === 'send' ? 0 : 2;
}

function cmdSeal(args) {
  const { ledger } = loadStore(args.file);
  const desk = new MailDesk(ledger.view());
  const senderRec = JSON.parse(fs.readFileSync(args.sender ?? args.file, 'utf8'));
  const parcel = desk.submit({
    kind: args.material,
    recipientName: args.name,
    payload: JSON.parse(args.body),
    senderId: 'cli-sender',
    senderSignPrivate: senderRec.vaultSignPrivate,
  });
  if (parcel.routing.decision !== 'send') {
    console.log(`暂扣/封死：${parcel.routing.reason}`);
    process.exitCode = 2;
    return;
  }
  fs.writeFileSync(args.out, JSON.stringify(parcel, null, 2));
  console.log(`已封缄 ${parcel.parcelId} -> ${args.out}`);
}

function cmdOpen(args) {
  const parcel = JSON.parse(fs.readFileSync(args.parcel, 'utf8'));
  const encPrivate = fs.readFileSync(args.key, 'utf8').trim();
  const header = {
    parcelId: parcel.parcelId,
    kind: parcel.kind,
    senderId: parcel.senderId,
    recipientName: parcel.recipientName,
    relatedPhotoId: parcel.relatedPhotoId,
    ...(parcel.reSealedFrom ? { reSealedFrom: parcel.reSealedFrom } : {}),
    createdAt: parcel.createdAt,
  };
  console.log(JSON.stringify(openSeal(encPrivate, parcel.envelope, header), null, 2));
}

const [cmd, ...rest] = process.argv.slice(2);
const args = parseArgs(rest);
({
  'init-ledger': cmdInitLedger,
  append: (a) => cmdAppend(a, 'rotate'),
  add: (a) => cmdAppend(a, 'add'),
  revoke: cmdRevoke,
  fork: cmdFork,
  view: cmdView,
  route: cmdRoute,
  seal: cmdSeal,
  open: cmdOpen,
}[cmd] ?? (() => {
  console.log('未知命令。可用：init-ledger | add | append | revoke | fork | view | route | seal | open');
  process.exit(1);
}))(args);
