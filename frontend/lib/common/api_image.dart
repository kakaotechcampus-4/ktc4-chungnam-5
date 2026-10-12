import 'package:flutter/material.dart';

import '../api/api_client.dart';

/// 서버가 준 사진 경로(`/media/...`)를 그린다. 사진은 이 위젯으로만 그린다.
///
/// `Image.network` 는 dio 를 거치지 않아 `X-Demo-Key` 가 자동으로 붙지 않는다.
/// 데모 서버에서는 키 없는 사진 요청이 403 이라, 화면마다 헤더를 챙기다
/// 하나라도 빠지면 그 화면 사진만 안 뜬다. 주소 변환([ApiConfig.mediaUrl])과
/// 헤더를 여기 한 곳에 둔다. 나중에 사진이 S3 presigned URL 로 바뀌면 이
/// 위젯만 고친다.
class ApiImage extends StatelessWidget {
  const ApiImage(this.path, {super.key, this.fit = BoxFit.cover});

  /// 서버가 준 경로. 전체 주소여도 된다.
  final String path;
  final BoxFit fit;

  @override
  Widget build(BuildContext context) {
    return Image.network(
      ApiConfig.mediaUrl(path),
      headers: ApiConfig.demoKeyHeaders,
      fit: fit,
      // 사진이 없어도 화면은 그대로 쓸 수 있어야 한다 — 빈 자리로 둔다.
      errorBuilder: (_, _, _) => const SizedBox(),
    );
  }
}
