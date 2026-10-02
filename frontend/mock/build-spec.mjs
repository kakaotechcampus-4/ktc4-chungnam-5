// BE 스키마(openapi.json)에 고정 응답(examples.json)을 끼워 Prism 이 읽을 파일을 만든다.
//
// mock 서버는 로직이 없다 — 어떤 요청이 와도 여기 적힌 응답만 돌려준다.
// 예시가 없는 API 는 Prism 이 스키마에서 기본값("string", 0)을 만들어 준다.
//
// 날짜는 고정이면 "오늘" 화면이 늘 비어 보여서, 서버를 띄우는 날 기준으로만 바꾼다.
//   {{today}} · {{today-3}} · {{today+2}} → YYYY-MM-DD, {{month}} → YYYY-MM

import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';

const here = new URL('.', import.meta.url);
const spec = JSON.parse(readFileSync(new URL('openapi.json', here), 'utf8'));
const examples = JSON.parse(readFileSync(new URL('examples.json', here), 'utf8'));

const pad = (n) => String(n).padStart(2, '0');
const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

function fillDates(text) {
  const today = new Date();
  return text
    .replace(/\{\{today([+-]\d+)?\}\}/g, (_, offset) => {
      const d = new Date(today);
      d.setDate(d.getDate() + Number(offset ?? 0));
      return ymd(d);
    })
    .replace(/\{\{month\}\}/g, ymd(today).slice(0, 7));
}

const filled = JSON.parse(fillDates(JSON.stringify(examples)));

for (const [route, byStatus] of Object.entries(filled)) {
  const [method, path] = route.split(' ');
  const operation = spec.paths[path]?.[method.toLowerCase()];
  if (!operation) throw new Error(`openapi.json 에 없는 API: ${route}`);
  for (const [status, body] of Object.entries(byStatus)) {
    const content = operation.responses[status]?.content?.['application/json'];
    if (!content) throw new Error(`${route} 에 ${status} 응답이 없음`);
    content.example = body;
  }
}

mkdirSync(new URL('.build/', here), { recursive: true });
writeFileSync(new URL('.build/openapi.mock.json', here), JSON.stringify(spec));
console.log(`고정 응답 ${Object.keys(filled).length}개를 넣었어요 → .build/openapi.mock.json`);
