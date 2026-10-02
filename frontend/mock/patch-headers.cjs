// Prism 은 요청 검사 결과를 `sl-violations` 응답 헤더에 그대로 넣는다. 메시지에 한글
// (약 이름 enum 등)이 섞이면 Node 가 "Invalid character in header" 로 서버째 죽는다.
// 헤더 값의 ASCII 밖 글자만 퍼센트 인코딩해서 막는다. 응답 body 는 건드리지 않는다.
const http = require('node:http');

const setHeader = http.ServerResponse.prototype.setHeader;
const toAscii = (v) =>
  typeof v === 'string' ? v.replace(/[^\x20-\x7e]/g, encodeURIComponent) : v;

http.ServerResponse.prototype.setHeader = function (name, value) {
  return setHeader.call(this, name, Array.isArray(value) ? value.map(toAscii) : toAscii(value));
};
