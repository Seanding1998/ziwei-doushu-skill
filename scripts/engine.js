#!/usr/bin/env node
/**
 * ziwei-doushu / scripts/engine.js
 * iztro 计算引擎 —— 由 Python 调用，读 stdin JSON、写 stdout JSON。
 * 本文件不做解读、不做格式判断，只把 iztro 的确定性数据原样吐出。
 *
 * 输入（stdin JSON）:
 *   { "date": "1990-06-07", "calendar": "solar"|"lunar", "timeIndex": 4,
 *     "gender": "男", "years": [2026, 2027], "fixLeap": true }
 * 输出（stdout JSON）: { ok: true, engine, version, astrolabe: {...} } 或 { ok:false, error, message }
 */
'use strict';

let iztro = null;
const errors = [];
for (const spec of ['iztro', 'iztro/lib/index.js']) {
  try { iztro = require(spec); break; } catch (e) { errors.push(String(e.message)); }
}
if (!iztro) {
  process.stdout.write(JSON.stringify({
    ok: false, error: 'IZTRO_NOT_FOUND',
    message: '未找到 iztro 模块。请先运行: python ziwei.py deps',
    detail: errors,
  }));
  process.exit(3);
}
const { astro } = iztro;

/**
 * 节气四柱（立春分界）—— 由 lunar-lite 提供（iztro 自身的历法依赖），
 * 权威且不必自己近似节气交接时刻。
 */
function jieqiPillar(dateStr, timeIndex) {
  let lunar = null;
  for (const spec of ['lunar-lite']) {
    try { lunar = require(spec); break; } catch (e) { /* next */ }
  }
  if (!lunar || typeof lunar.getHeavenlyStemAndEarthlyBranchBySolarDate !== 'function') return null;
  try {
    const r = lunar.getHeavenlyStemAndEarthlyBranchBySolarDate(dateStr, Number(timeIndex) || 0);
    const j = (x) => (Array.isArray(x) ? x.join('') : String(x));
    return {
      yearly: j(r.yearly), monthly: j(r.monthly), daily: j(r.daily), hourly: j(r.hourly),
      text: [j(r.yearly), j(r.monthly), j(r.daily), j(r.hourly)].join(' '),
    };
  } catch (e) {
    return null;
  }
}

function readStdin() {
  return new Promise((resolve, reject) => {
    let buf = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (c) => { buf += c; });
    process.stdin.on('end', () => resolve(buf));
    process.stdin.on('error', reject);
  });
}

const pick = (s) => (!s ? null : { name: s.name, brightness: s.brightness || null, mutagen: s.mutagen || null, scope: s.scope || null });
const pickAll = (arr) => (arr || []).map(pick);

function main(req) {
  const { date, calendar = 'solar', timeIndex, gender, years = [], fixLeap = true } = req;
  if (!date) return { ok: false, error: 'MISSING_DATE' };
  if (timeIndex === undefined || timeIndex === null) return { ok: false, error: 'MISSING_TIME_INDEX' };
  if (!gender) return { ok: false, error: 'MISSING_GENDER' };

  let a;
  try {
    a = calendar === 'lunar'
      ? astro.astrolabeByLunarDate(date, Number(timeIndex), gender, false, fixLeap, 'zh-CN')
      : astro.astrolabeBySolarDate(date, Number(timeIndex), gender, true, 'zh-CN');
  } catch (e) {
    return { ok: false, error: 'IZTRO_THREW', message: String(e && e.message || e) };
  }

  const palaces = a.palaces.map((p) => ({
    name: p.name,
    stem: p.heavenlyStem,
    branch: p.earthlyBranch,
    isBodyPalace: !!p.isBodyPalace,
    major: pickAll(p.majorStars),
    minor: pickAll(p.minorStars),
    adjective: (p.adjectiveStars || []).map((s) => s.name),
    spirits: {
      suiqian: p.suiqian12 || null,
      jiangqian: p.jiangqian12 || null,
      changsheng: p.changsheng12 || null,
      boshi: p.boshi12 || null,
    },
    decadalRange: p.decadal ? p.decadal.range : null,
  }));

  const decadal = a.decadalList().map((x) => ({
    ageRange: x.ageRange,
    yearRange: x.yearRange,
    ganzhi: x.heavenlyStem + x.earthlyBranch,
    stem: x.heavenlyStem,
    branch: x.earthlyBranch,
    palace: x.palaceName,
    mutagen: [x.mutagen[0], x.mutagen[1], x.mutagen[2], x.mutagen[3]],
  }));

  const horoscope = [];
  for (const y of years) {
    let h;
    try { h = a.horoscope(new Date(`${y}-06-15T12:00:00`)); } catch (e) { continue; }
    const flat = (arr, names) => {
      const out = [];
      (arr || []).forEach((grp, i) => {
        const pn = (names && names[i]) || '';
        for (const s of (Array.isArray(grp) ? grp : [grp])) {
          if (s) out.push({ palace: pn, name: s.name, brightness: s.brightness || null, mutagen: s.mutagen || null, scope: s.scope || null });
        }
      });
      return out;
    };
    horoscope.push({
      year: Number(y),
      nominalAge: h.age ? h.age.nominalAge : null,
      soulLimit: h.age ? { ganzhi: h.age.heavenlyStem + h.age.earthlyBranch, branch: h.age.earthlyBranch } : null,
      decadal: {
        ganzhi: h.decadal.heavenlyStem + h.decadal.earthlyBranch,
        mutagen: [h.decadal.mutagen[0], h.decadal.mutagen[1], h.decadal.mutagen[2], h.decadal.mutagen[3]],
        stars: flat(h.decadal.stars, h.decadal.palaceNames),
      },
      yearly: {
        ganzhi: h.yearly.heavenlyStem + h.yearly.earthlyBranch,
        mutagen: [h.yearly.mutagen[0], h.yearly.mutagen[1], h.yearly.mutagen[2], h.yearly.mutagen[3]],
        stars: flat(h.yearly.stars, h.yearly.palaceNames),
      },
      monthly: h.monthly ? { ganzhi: h.monthly.heavenlyStem + h.monthly.earthlyBranch } : null,
    });
  }

  return {
    ok: true,
    engine: 'iztro',
    version: (() => { try { return require('iztro/package.json').version; } catch (e) { return 'unknown'; } })(),
    astrolabe: {
      solarDate: a.solarDate,
      lunarDate: a.lunarDate,
      chineseDate: a.chineseDate,
      fiveElementsClass: a.fiveElementsClass,
      soul: a.soul,
      body: a.body,
      soulPalaceBranch: a.earthlyBranchOfSoulPalace,
      bodyPalaceBranch: a.earthlyBranchOfBodyPalace,
      sign: a.sign,
      zodiac: a.zodiac,
      time: a.time,
      timeRange: a.timeRange,
      palaces,
      decadal,
      horoscope,
      // 节气四柱（立春分界，八字口径）——仅供对照，紫微斗数不用它取四化
      jieqiPillar: jieqiPillar(date, timeIndex),
    },
  };
}

readStdin().then((raw) => {
  let req;
  try { req = JSON.parse(raw || '{}'); } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: 'BAD_JSON', message: String(e.message) }));
    return;
  }
  let res;
  try { res = main(req); } catch (e) {
    res = { ok: false, error: 'ENGINE_THREW', message: String(e && e.stack || e) };
  }
  process.stdout.write(JSON.stringify(res));
}).catch((e) => {
  process.stdout.write(JSON.stringify({ ok: false, error: 'STDIN_FAIL', message: String(e) }));
});
