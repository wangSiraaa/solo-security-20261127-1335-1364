import {
  absentNextDayScenario,
  consentAndHeldReceiptScenario,
  faultCleanupScenario,
  happyPathStartStopQuerySplit,
  midRevocationScenario,
  noticeAndLifecycleScenario,
  startHarness,
} from './scenarios.mjs';

const scenarios = [
  ['实际开段 / 停段 / 新工程轨道查询 / 分轨', happyPathStartStopQuerySplit],
  ['同意收窄为准试听禁存 + 回执滞留不得签完结证明', consentAndHeldReceiptScenario],
  ['次日缺席与服务重启', absentNextDayScenario],
  ['中途主动撤销', midRevocationScenario],
  ['新说明不等于授权 + 设备生命周期', noticeAndLifecycleScenario],
  ['故障清理绑定', faultCleanupScenario],
];

let failures = 0;
for (const [name, scenario] of scenarios) {
  const h = await startHarness({ label: name.replace(/[^\w一-龥]+/g, '-').slice(0, 20) });
  process.stdout.write(`\n▶ ${name}\n`);
  try {
    const result = await scenario(h, (message) => process.stdout.write(`  • ${message}\n`));
    process.stdout.write(`✅ PASS ${name}${result ? ` ${JSON.stringify(result)}` : ''}\n`);
  } catch (error) {
    failures += 1;
    process.stdout.write(`❌ FAIL ${name}\n`);
    console.error(error);
  } finally {
    await h.close();
  }
}

if (failures > 0) {
  console.error(`\n${failures} scenario(s) failed.`);
  process.exit(1);
}
console.log('\n全部验收场景通过：旧“录一段”没有升级成常驻监听。');
