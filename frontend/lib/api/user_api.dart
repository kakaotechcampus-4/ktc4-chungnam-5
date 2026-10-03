import 'api_client.dart';

/// 회원 정보. `POST /users/profile` · `GET /users/me` 응답(`data` 안쪽).
///
/// 프로필 입력 화면과 마이 탭이 같이 써서 화면 파일이 아니라 여기 둔다.
class UserProfile {
  const UserProfile({
    required this.userId,
    required this.nickname,
    required this.heightCm,
    required this.weightKg,
    required this.baselineIntake,
    required this.onboardingStatus,
  });

  final String userId;
  final String nickname;
  final double? heightCm;

  /// 최근 기록 체중. 컨디션 기록 팝업에서 새로 적으면 바뀐다.
  final double? weightKg;

  /// 투약 전 평소 한 끼 열량(kcal). 양(Quantity) 점수의 기준.
  final double baselineIntake;

  /// `PROFILE_REQUIRED` · `MEDICATION_REQUIRED` · `READY`.
  final String onboardingStatus;

  factory UserProfile.fromJson(Map<String, dynamic> json) => UserProfile(
    userId: json['userId'] as String,
    nickname: json['nickname'] as String,
    heightCm: (json['heightCm'] as num?)?.toDouble(),
    weightKg: (json['weightKg'] as num?)?.toDouble(),
    baselineIntake: (json['baselineIntake'] as num).toDouble(),
    onboardingStatus: json['onboardingStatus'] as String,
  );
}

/// 회원 API.
class UserApiService {
  UserApiService(this._client);

  final ApiClient _client;

  /// `POST /users/profile` — 지금은 이 호출이 곧 사용자 생성이다.
  Future<UserProfile> createProfile({
    required String nickname,
    required double heightCm,
    required double weightKg,
    required double baselineIntake,
  }) async {
    final result = await _client.post(
      '/users/profile',
      body: {
        'nickname': nickname,
        'heightCm': heightCm,
        'weightKg': weightKg,
        'baselineIntake': baselineIntake,
      },
    );
    return UserProfile.fromJson(result.dataMap);
  }

  /// `PATCH /users/me` — 준 필드만 바꾼다. 응답은 `GET /users/me` 와 같다.
  ///
  /// 체중은 덮어쓰기가 아니라 **새 체중 기록**이다 — 서버는 체중을
  /// `user_states` 에만 두고, 여기서 온 값도 그날의 기록 1건으로 남긴다.
  Future<UserProfile> updateProfile({
    String? nickname,
    double? heightCm,
    double? weightKg,
    double? baselineIntake,
  }) async {
    final body = {
      'nickname': ?nickname,
      'heightCm': ?heightCm,
      'weightKg': ?weightKg,
      'baselineIntake': ?baselineIntake,
    };
    final result = await _client.patch('/users/me', body: body);
    return UserProfile.fromJson(result.dataMap);
  }

  /// `GET /users/me`
  Future<UserProfile> fetchMe() async {
    final result = await _client.get('/users/me');
    return UserProfile.fromJson(result.dataMap);
  }
}
