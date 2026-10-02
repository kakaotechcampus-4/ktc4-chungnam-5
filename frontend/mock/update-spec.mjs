// 실행 중인 BE(dev)에서 최신 스키마를 받아 openapi.json 을 덮어쓴다.
// BE API 가 바뀌면 이걸 돌리고, 바뀐 응답 모양에 맞게 examples.json 도 고친다.
//
//   npm run update-spec                       # 기본: http://localhost:8000/openapi.json
//   BE_URL=http://<주소>/openapi.json npm run update-spec

import { writeFileSync } from 'node:fs';

const url = process.env.BE_URL ?? 'http://localhost:8000/openapi.json';
const response = await fetch(url);
if (!response.ok) throw new Error(`${url} → ${response.status}`);
const spec = await response.json();
writeFileSync(new URL('openapi.json', import.meta.url), JSON.stringify(spec, null, 2) + '\n');
console.log(`${url} 에서 API ${Object.keys(spec.paths).length}개 경로를 받았어요`);
