// 端到端场景：两处分库离线换钥 → 碰头对账 → 寄送台裁决 → 封件全流程 → 局部结价。
// 运行：node demo/scenario.mjs
import {
  Ledger,
  RosterView,
  Seat,
  Vault,
  Witness,
  rosterEntry,
  revokeEntry,
  MailDesk,
  ShiftRoster,
  MATERIALS,
  WorkLedger,
  verifySignedJson,
  pageId,
  pageBody,
  fingerprint,
} from '../src/index.mjs';

const line = (t) => console.log('\n' + '─'.repeat(72) + '\n' + t + '\n' + '─'.repeat(72));
const ok = (cond, msg) => {
  if (!cond) {
    console.error('✗ 断言失败：' + msg);
    process.exitCode = 1;
    throw new Error(msg);
  }
  console.log('  ✓ ' + msg);
};

// =====================================================================
line('一、共同旧册：两处分库与独立见证席从同一份创世页出发');
// =====================================================================
const vaultA = new Vault('分库A');
const vaultB = new Vault('分库B');
const witness1 = new Witness('见证席一');
const witness2 = new Witness('见证席二');
vaultA.publishKeyMaps([vaultB], [witness1, witness2]);
vaultB.publishKeyMaps([vaultA], [witness1, witness2]);

// 收件席：照片与报价可能交给不同的人
const photoSeat = new Seat('照片收件席');
photoSeat.generateKey(0);
const quoteSeat = new Seat('报价收件席');
quoteSeat.generateKey(0);
const backupSeat = new Seat('备用接收席'); // 共同认可的第二把接收钥
backupSeat.generateKey(0);

const genesis = vaultA.ledger.append([
  { ...rosterEntry('add', '照片处', photoSeat, { materialsKinds: ['photo'] }) },
  { ...rosterEntry('add', '报价处', quoteSeat, { materialsKinds: ['quote'] }) },
  { ...rosterEntry('add', '备用处', backupSeat, { materialsKinds: ['photo', 'quote'] }) },
]);
witness1.holdBase(pageId(genesis));
witness2.holdBase(pageId(genesis));
witness1.attest(vaultA.ledger, genesis, ['照片处', '报价处', '备用处']);
witness2.attest(vaultA.ledger, genesis, ['照片处', '报价处', '备用处']);
// 分库B 拿同一份页签收（本地见过），两边链完全一致
vaultB.intake(genesis);
ok(vaultB.ledger.index.has(pageId(genesis)), '分库B 受理共同创世页并本地签收');
ok(genesis.attestations.length === 2, '两名独立见证席对同一条延长记录作证');

// =====================================================================
line('二、离线：两库从同一旧册各带续写依据分叉，页数相同也接不到同一页');
// =====================================================================
// 分库A：报价处诚实换钥（rotate），并明文撤销备用处
const oldQuoteKey = quoteSeat.keyAt(0);
quoteSeat.rotateKey();
const pageA1 = vaultA.ledger.append([
  rosterEntry('rotate', '报价处', quoteSeat, { materialsKinds: ['quote'] }),
  revokeEntry('备用处', backupSeat),
]);
witness1.attest(vaultA.ledger, pageA1, ['报价处', '备用处']);

// 分库B：同名“报价处”走了另一条分支（另起一把同代钥），且仍以为备用处在任
const branchQuoteSeat = new Seat('报价收件席·B分支');
branchQuoteSeat.generateKey(0);
const oneSideSeat = makeSeatWithKey('单侧席');
const pageB1 = vaultB.ledger.append([
  rosterEntry('add', '报价处', branchQuoteSeat, { materialsKinds: ['quote'] }),
  rosterEntry('add', '新地址·单侧', oneSideSeat, { materialsKinds: ['photo'] }),
]);
vaultB.ledger.markIntake(pageB1, '分库B', vaultB.sign.privatePkcs8);
function makeSeatWithKey(id) {
  const s = new Seat(id);
  s.generateKey(0);
  return s;
}

ok(pageA1.seq === pageB1.seq, `两页页号相同（均为 seq=${pageA1.seq}），但前页之后内容分叉`);
ok(pageId(pageA1) !== pageId(pageB1), '同页号不同内容 → 页哈希不同，无法接到同一份旧页');

// 见证席只签自己旧页的延长：让见证席核对 B 的页，它接在共同创世页上，本应可签；
// 但只按名见证“报价处”，单侧新地址不在任何见证范围内。
witness1.attest(vaultB.ledger, pageB1, ['报价处']);
const foreign = new Vault('野分库');
foreign.publishKeyMaps([], [witness1]);
const foreignPage = foreign.ledger.append([
  rosterEntry('add', '野名册地址', makeSeatWithKey('野席'), { materialsKinds: ['photo'] }),
]);
let refused = false;
try {
  witness1.attest(foreign.ledger, foreignPage);
} catch (e) {
  refused = true;
  console.log('    拒签原因：' + e.message);
}
ok(refused, '独立见证席拒绝为接不到自己旧页的异链延长作证');

// =====================================================================
line('三、离线查册器：共有最后一页 + 双方分别可验真的分歧');
// =====================================================================
vaultA.bringBackLoose([pageB1]);
vaultB.bringBackLoose([pageA1]);
const report = vaultA.ledger.reconcile(vaultB.ledger);
ok(report.kind === 'fork', '检出分叉而非静默覆盖');
ok(report.commonBaseId === pageId(genesis), '共有最后一页 = 共同创世页');
ok(report.mineOnly.length === 1 && report.theirsOnly.length === 1, '两侧各有一页独有延长');

// 分库A 独立验真分库B 的页签名（不依赖 B 在场）
const bPageVerified = verifySignedJson(
  vaultB.sign.publicSpki,
  pageBody(report.theirsOnly[0]),
  report.theirsOnly[0].signature,
);
ok(bPageVerified, '分库A 用分库B公钥独立验真对方分叉页');

// =====================================================================
line('四、碰头并账：撤销并集永久黏住；同名新钥双分支冲突待裁决');
// =====================================================================
const { mergedPage, conflicts } = vaultA.ledger.merge(
  vaultB.ledger,
  '分库B',
  vaultB.sign.publicSpki,
);
vaultA.ledger.countersignMerge(mergedPage, vaultB.sign.privatePkcs8);
vaultB.intake(mergedPage);
// 并账后，见证席把双签合并页作为新的持有旧页（它仍是共同创世页的延长）
witness1.holdBase(pageId(mergedPage));
witness2.holdBase(pageId(mergedPage));
ok(mergedPage.mergeProof.countersignatures.length === 1, '合并页取得分库B双签 → 双方共同认可');
ok(conflicts.some((c) => c.name === '报价处'), '同名“报价处”两把新钥被列为冲突，不静默选边');

const mergedView = vaultA.ledger.view();
ok(mergedView.lookup('备用处')?.status === 'revoked', '撤销并集：备用处在并账名册中保持撤销');
const addAgain = vaultA.ledger.append([
  rosterEntry('add', '备用处', backupSeat, { materialsKinds: ['photo'] }),
]);
const afterReadd = vaultA.ledger.view();
ok(afterReadd.lookup('备用处').status === 'revoked', '共同旧页之后重新 add 被撤名 → 撤销黏住，被撤钥不能复活');

// =====================================================================
line('五、寄送台裁决：共同认可的在任地址可寄；单侧/暂扣地址与被撤钥拦截');
// =====================================================================
const desk = new MailDesk(mergedView);
const senderSeat = makeSeatWithKey('发件工作台');

const photo = { photoId: 'P-100', title: '青铜器局部照片', damageMarks: ['裂隙A'] };
const photoParcel = desk.submit({
  kind: MATERIALS.PHOTO,
  recipientName: '照片处',
  payload: photo,
  senderId: senderSeat.seatId,
  senderSignPrivate: senderSeat.sign.privatePkcs8,
});
const quotePayload = { photoId: 'P-100', region: { x: 0, y: 0, w: 4, h: 4 }, unitPrice: 10 };
const quoteParcel = desk.submit({
  kind: MATERIALS.QUOTE,
  recipientName: '报价处',
  payload: quotePayload,
  senderId: senderSeat.seatId,
  senderSignPrivate: senderSeat.sign.privatePkcs8,
});
const backupParcel = desk.submit({
  kind: MATERIALS.PHOTO,
  recipientName: '备用处',
  payload: { photoId: 'P-101' },
  senderId: senderSeat.seatId,
  senderSignPrivate: senderSeat.sign.privatePkcs8,
});
const oneSideParcel = desk.submit({
  kind: MATERIALS.PHOTO,
  recipientName: '新地址·单侧',
  payload: { photoId: 'P-102' },
  senderId: senderSeat.seatId,
  senderSignPrivate: senderSeat.sign.privatePkcs8,
});
ok(photoParcel.routing.decision === 'send', '照片处：共同认可且在任 → 可寄');
ok(quoteParcel.routing.decision === 'hold', '报价处：双分支冲突未裁决 → 暂扣');
ok(backupParcel.routing.decision === 'block', '备用处：明文撤销 → 封死，共同旧页也救不回');
ok(oneSideParcel.routing.decision === 'hold', '分库B单侧新地址：仅本地签收无见证/双签 → 暂扣');

// =====================================================================
line('六、返场轮班单：两个经办人各允收不同材料，原发件入口统一处理');
// =====================================================================
const shifts = new ShiftRoster();
shifts.addOperator({
  operatorId: '经办人甲',
  seatId: photoSeat.seatId,
  allowedMaterials: ['photo'],
  recipientNames: ['照片处', '备用处'],
});
shifts.addOperator({
  operatorId: '经办人乙',
  seatId: quoteSeat.seatId,
  allowedMaterials: ['quote'],
  recipientNames: ['报价处'],
});
const r1 = desk.dispatch(photoParcel.parcelId, shifts);
const r2 = desk.dispatch(quoteParcel.parcelId, shifts);
const r3 = desk.dispatch(backupParcel.parcelId, shifts);
const r4 = desk.dispatch(oneSideParcel.parcelId, shifts);
ok(r1.dispatched, '照片在途（经办人甲当班允收照片）');
ok(!r2.dispatched, '报价仍暂扣：轮班单不能绕过名册冲突');
ok(!r3.dispatched && backupParcel.status === 'held', '被撤钥封件进入暂扣箱');
ok(!r4.dispatched && oneSideParcel.status === 'held', '单侧地址封件暂扣待对账');

// =====================================================================
line('七、收件席只凭实收封件与手里钥匙开件；已交照片按回执对账');
// =====================================================================
const openedPhoto = desk.open(photoParcel.parcelId, photoSeat.activeKey().enc.privatePkcs8);
ok(openedPhoto.photoId === 'P-100', '照片收件席用自己的钥匙开出照片正文');
const receipt = desk.acknowledge(
  photoParcel.parcelId,
  photoSeat.activeKey().enc.privatePkcs8,
  photoSeat.activeKey().sign.privatePkcs8,
);
const recon = desk.reconcileReceipt(receipt);
ok(recon.ok, '已交照片凭真正收件席签名回执完成对账销账');
// 伪造回执：用别人的签名钥
import { signJson } from '../src/crypto.mjs';
const fakeReceipt = {
  ...receipt,
  receiptId: 'rcpt_fake',
  signature: signJson(
    branchQuoteSeat.activeKey().sign.privatePkcs8,
    { kind: 'delivery-receipt', receipt: { ...receipt, receiptId: 'rcpt_fake' } },
  ),
};
ok(!desk.reconcileReceipt(fakeReceipt).ok, '非真正收件席签的回执对账失败');

// =====================================================================
line('八、待重封报价：换钥后改寄，旧钥打不开新封件');
// =====================================================================
// 裁决同名冲突：采用分库A分支的诚实换钥（rotate 链），出决定页并请见证席见证
const resolvePage = vaultA.ledger.resolveConflict(
  '报价处',
  rosterEntry('rotate', '报价处', quoteSeat, { materialsKinds: ['quote'] }),
);
witness2.attest(vaultA.ledger, resolvePage, ['报价处']); // 见证席确认这是共同旧页的合法延长
const resolvedView = vaultA.ledger.view();
desk.setView(resolvedView);
ok(resolvedView.routingDecision('报价处', 'quote').decision === 'send', '冲突裁决并有见证依据 → 报价处恢复可寄');

const reseal = desk.reSealQuote(quoteParcel.parcelId, senderSeat.sign.privatePkcs8);
ok(reseal.ok, '待发箱报价按新在任钥重封改寄');
const newQuote = reseal.parcel;
ok(
  MailDesk.oldKeyCannotOpen(newQuote, oldQuoteKey.enc.privatePkcs8),
  '被换掉的旧报价钥无法打开改寄后的新报价封件（GCM 认证失败）',
);
const openedQuote = desk.open(newQuote.parcelId, quoteSeat.activeKey().enc.privatePkcs8);
ok(openedQuote.region.w === 4, '现任报价收件席用新钥开出报价正文');
ok(quoteParcel.status === 'superseded', '旧报价封件标记被取代，不再投递');
const r5 = desk.dispatch(newQuote.parcelId, shifts);
ok(r5.dispatched, '重封报价经轮班单在途（经办人乙）');
const quoteReceipt = desk.acknowledge(
  newQuote.parcelId,
  quoteSeat.activeKey().enc.privatePkcs8,
  quoteSeat.activeKey().sign.privatePkcs8,
);
ok(desk.reconcileReceipt(quoteReceipt).ok, '报价交付回执对账通过');

const bins = desk.bins();
console.log(
  '    分箱：在途', bins.intransit.length,
  '| 已交', bins.delivered.length,
  '| 暂扣', bins.held.length,
  '| 被取代', bins.superseded.length,
);
ok(bins.delivered.length === 2 && bins.held.length === 2 && bins.superseded.length === 1, '封件状态分箱正确');

// =====================================================================
line('九、局部病害图：新图重叠两张旧图不重复结价；对不准单独挂起');
// =====================================================================
const work = new WorkLedger();
const q1 = work.registerQuote({ quoteId: 'Q1', photoId: 'P-100', region: { x: 0, y: 0, w: 4, h: 4 }, unitPrice: 10, anchor: 'A1', ownerSeat: '报价收件席' });
const q2 = work.registerQuote({ quoteId: 'Q2', photoId: 'P-100', region: { x: 3, y: 3, w: 4, h: 4 }, unitPrice: 10, anchor: 'A2', ownerSeat: '报价收件席' });
// 新局部图同时压住 Q1、Q2
const q3 = work.registerQuote({ quoteId: 'Q3', photoId: 'P-100', region: { x: 2, y: 2, w: 4, h: 4 }, unitPrice: 10, anchor: 'A1', ownerSeat: '报价收件席' });
// 离群、对不准任何旧图的一笔
const q4 = work.registerQuote({ quoteId: 'Q4', photoId: 'P-100', region: { x: 40, y: 40, w: 2, h: 2 }, unitPrice: 10, anchor: 'ZZ', ownerSeat: '报价收件席' });

const successor = new Seat('接班席');
successor.generateKey(0);
const view1 = WorkLedger.seatView(work, successor.seatId, {
  allowedRegionIds: ['Q1', 'Q2', 'Q3', 'Q4'],
  allowedAnchors: ['A1', 'A2', 'ZZ'],
});
const wi1 = work.assignToSeat(successor.seatId, view1);
const overlapCells = q1.cells.filter((c) => q2.cells.includes(c));
ok(overlapCells.length === 1, 'Q1/Q2 在单元 (3,3) 重叠');
const wi1CellSet = new Set(wi1.cells);
ok(
  [...work.settledCells.keys()].length === new Set([...work.settledCells.keys()]).size &&
    wi1.cells.length === new Set(wi1.cells).size,
  '每个网格单元只结一次价（重叠区不重复结价）',
);
ok(wi1.suspended.includes('Q4'), '对不准的 Q4 整笔单独挂起，未并入相邻工项');
work.closeWorkItem(wi1.workItemId);

// 第二接班席：只能取获准局部与自己的定位点，看不到未授权区域
const successor2 = new Seat('接班席二');
successor2.generateKey(0);
const view2 = WorkLedger.seatView(work, successor2.seatId, {
  allowedRegionIds: ['Q2', 'Q3'],
  allowedAnchors: ['A2'], // 只许 A2 定位点
});
ok(!view2.regions.some((r) => r.quoteId === 'Q1'), '接班席二看不到未获准的 Q1 局部');
ok(!view2.regions.some((r) => r.quoteId === 'Q3'), 'Q3 定位点 A1 不在其权限内 → 同样不可见');
ok(view2.closedRecords.length === 1, '已结案记录对接班席可见（用于避让重复结价）');
const wi2 = work.assignToSeat(successor2.seatId, view2);
ok(wi2.quoteIds.length === 0 || wi2.cells.every((c) => !new Set(wi1.cells).has(c)), '相邻工项不占用已结案单元，避免两笔重复结价');

// =====================================================================
line('十、两个非破坏性还原实验（只在内存视图上试，不改写已签旧页）');
// =====================================================================
// 实验1：只把名册里的同名新钥切到另一条分支，看哪些封件须扣
const altView = new RosterView([...vaultA.ledger.chainPages()]);
// 手工注入 B 分支条目模拟“换分支裁决”
altView.byName.set('报价处', {
  name: '报价处',
  status: 'active',
  lastEntry: pageB1.entries.find((e) => e.name === '报价处'),
  history: [],
  attestations: [{ witnessId: '见证席一' }],
  intakeReceipts: [],
  pageId: pageId(pageB1),
  merged: false,
});
const altDesk = new MailDesk(altView);
const altQuote = altDesk.submit({
  kind: MATERIALS.QUOTE,
  recipientName: '报价处',
  payload: quotePayload,
  senderId: senderSeat.seatId,
  senderSignPrivate: senderSeat.sign.privatePkcs8,
});
ok(altQuote.routing.decision === 'send', '实验1：切到B分支后，按B分支钥匙可寄');
// A 分支重封封件的收件钥与 B 分支不同 → 已按 A 钥封的新报价必须暂扣
const aKeyFp = fingerprint(quoteSeat.activeKey().enc.publicSpki);
const bKeyFp = fingerprint(branchQuoteSeat.activeKey().enc.publicSpki);
ok(aKeyFp !== bKeyFp, '实验1：两分支同名钥指纹不同，已按A分支封缄的报价在B裁决下须扣');
console.log('    还原：实验仅构造 altView，原名册页与已出回执均未改动');

// 实验2：还原名册，只把报价接班人调走 → 不受影响的照片交接仍能由真正收件席核实
const restoredView = vaultA.ledger.view(); // 重新从链折叠 = 还原
const restoredDesk = new MailDesk(restoredView);
const photoAgain = restoredDesk.submit({
  kind: MATERIALS.PHOTO,
  recipientName: '照片处',
  payload: { photoId: 'P-200' },
  senderId: senderSeat.seatId,
  senderSignPrivate: senderSeat.sign.privatePkcs8,
});
ok(photoAgain.routing.decision === 'send', '实验2：报价接班人调动不影响照片处，照片仍可寄');
const photoAgainReceipt = (() => {
  const p = restoredDesk.dispatch(photoAgain.parcelId, shifts);
  ok(p.dispatched, '实验2：照片照常进入在途');
  return restoredDesk.acknowledge(
    photoAgain.parcelId,
    photoSeat.activeKey().enc.privatePkcs8,
    photoSeat.activeKey().sign.privatePkcs8,
  );
})();
ok(restoredDesk.reconcileReceipt(photoAgainReceipt).ok, '实验2：照片回执由真正的照片收件席核实通过');

line('全部场景通过 ✔');
console.log('结论：当前可寄出的修复委托 = 照片处的照片委托；');
console.log('报价处委托在冲突裁决+见证后经重封寄出；备用处（已撤）与单侧新地址委托暂扣/封死。');
