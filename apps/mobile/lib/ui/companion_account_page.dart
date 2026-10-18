import 'package:flutter/material.dart';
import 'companion_surfaces.dart';
import '../data/host_session.dart';

class CompanionAccountPage extends StatelessWidget {
  const CompanionAccountPage({
    super.key,
    required this.connectionCard,
    required this.hasConnection,
    required this.hasSample,
    required this.onVoice,
    required this.onDisconnect,
    this.session,
    this.voiceStatus,
  });
  final HostSession? session;
  final String? voiceStatus;
  final Widget connectionCard;
  final bool hasConnection;
  final bool hasSample;
  final VoidCallback onVoice;
  final VoidCallback onDisconnect;
  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.fromLTRB(22, 12, 22, 22),
    children: [
      Material(
        color: Colors.transparent,
        borderRadius: BorderRadius.circular(18),
        child: InkWell(
          onTap: () => _accountDetails(context),
          borderRadius: BorderRadius.circular(18),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Row(
              children: [
                Container(
                  width: 58,
                  height: 58,
                  decoration: const BoxDecoration(
                    color: Color(0xffe7edfc),
                    shape: BoxShape.circle,
                  ),
                  child: const Icon(
                    Icons.person_outline,
                    size: 29,
                    color: Color(0xff5675bb),
                  ),
                ),
                const SizedBox(width: 15),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '账号与个人资料',
                        style: TextStyle(
                          fontSize: 18,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                      SizedBox(height: 6),
                      Text(
                        hasConnection
                            ? '${session?.name ?? '已保存账号'} · ${session?.role == 'owner' ? '设备主账号' : '设备成员'}'
                            : '扫描电脑二维码并使用正式账号登录',
                        style: TextStyle(
                          fontSize: 12,
                          color: Color(0xff697183),
                          height: 1.7,
                        ),
                      ),
                    ],
                  ),
                ),
                const Icon(
                  Icons.chevron_right,
                  size: 19,
                  color: Color(0xff8f97a6),
                ),
              ],
            ),
          ),
        ),
      ),
      const SizedBox(height: 31),
      companionSectionTitle('我的连接'),
      connectionCard,
      const SizedBox(height: 22),
      Material(
        color: Colors.white,
        borderRadius: BorderRadius.circular(24),
        clipBehavior: Clip.antiAlias,
        child: Column(
          children: [
            ListTile(
              contentPadding: const EdgeInsets.symmetric(
                horizontal: 18,
                vertical: 9,
              ),
              onTap: onVoice,
              leading: const Icon(
                Icons.mic_none_outlined,
                color: Color(0xff8090b1),
              ),
              title: const Text('我的声音样本', style: TextStyle(fontSize: 14)),
              subtitle: Text(
                voiceStatus ?? (!hasSample ? '读取电脑采样任务后录音' : '已保留此任务的录音'),
                style: const TextStyle(fontSize: 12, color: Color(0xff697183)),
              ),
              trailing: const Icon(
                Icons.chevron_right,
                size: 19,
                color: Color(0xff8f97a6),
              ),
            ),
            if (hasConnection) ...[
              const Divider(
                height: 1,
                indent: 18,
                endIndent: 18,
                color: Color(0xffeef0f5),
              ),
              ListTile(
                contentPadding: const EdgeInsets.symmetric(
                  horizontal: 18,
                  vertical: 9,
                ),
                onTap: onDisconnect,
                leading: const Icon(Icons.wifi, color: Color(0xff8090b1)),
                title: const Text('退出连接', style: TextStyle(fontSize: 14)),
                subtitle: const Text(
                  '手机上的素材与录音会保留',
                  style: TextStyle(fontSize: 12, color: Color(0xff697183)),
                ),
                trailing: const Icon(
                  Icons.chevron_right,
                  size: 19,
                  color: Color(0xff8f97a6),
                ),
              ),
            ],
          ],
        ),
      ),
      const SizedBox(height: 18),
      const Text(
        '选择视频和录音不会自动发送。连接后由主机核验身份，才会返回有权访问的内容。',
        style: TextStyle(fontSize: 12, color: Color(0xff697183), height: 1.7),
      ),
    ],
  );

  Future<void> _accountDetails(BuildContext context) =>
      showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        useSafeArea: true,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
        ),
        builder: (context) => Padding(
          padding: EdgeInsets.fromLTRB(
            22,
            16,
            22,
            22 + MediaQuery.paddingOf(context).bottom,
          ),
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              mainAxisSize: MainAxisSize.min,
              children: [
                Row(
                  children: [
                    const Expanded(
                      child: Text(
                        '账号与资料说明',
                        style: TextStyle(
                          fontSize: 20,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                    ),
                    IconButton(
                      tooltip: '关闭账号说明',
                      onPressed: () => Navigator.pop(context),
                      icon: const Icon(Icons.close, size: 20),
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                const Text(
                  '昵称和头像跟随正式账号统一管理。已有账号使用同一账号登录；新用户通过统一注册入口创建账号，再接受主账号的邀请。',
                  style: TextStyle(fontSize: 14, height: 1.8),
                ),
                const SizedBox(height: 18),
                Container(
                  padding: const EdgeInsets.all(16),
                  decoration: BoxDecoration(
                    color: const Color(0xfff0f3fa),
                    borderRadius: BorderRadius.circular(16),
                  ),
                  child: Text(
                    hasConnection
                        ? '当前连接已绑定正式账号、电脑和工作区。退出连接会移除此手机保存的登录凭证，原素材与录音保留。'
                        : '在电脑端打开手机连接并生成二维码，使用同一正式账号登录后配对。新成员请扫描电脑主账号生成的成员邀请。',
                    style: TextStyle(
                      fontSize: 13,
                      color: Color(0xff697183),
                      height: 1.8,
                    ),
                  ),
                ),
                const SizedBox(height: 18),
                const Text(
                  '素材可先保存在手机。录音使用电脑创建的采样任务和同一朗读文本；手机暂不提供注册或个人资料修改。',
                  style: TextStyle(
                    fontSize: 13,
                    color: Color(0xff697183),
                    height: 1.8,
                  ),
                ),
                const SizedBox(height: 24),
                FilledButton(
                  onPressed: () => Navigator.pop(context),
                  child: const Text('知道了'),
                ),
              ],
            ),
          ),
        ),
      );
}
