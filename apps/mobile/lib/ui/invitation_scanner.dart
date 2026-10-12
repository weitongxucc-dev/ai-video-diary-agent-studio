import 'dart:async';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

/// Returns the decoded content only. The caller must validate the invitation
/// before sending any credentials or treating the device as paired.
class InvitationScanner extends StatefulWidget {
  const InvitationScanner({super.key});

  @override
  State<InvitationScanner> createState() => _InvitationScannerState();
}

class _InvitationScannerState extends State<InvitationScanner> {
  final controller = MobileScannerController(formats: [BarcodeFormat.qrCode]);
  bool readingImage = false, returned = false;
  String? imageError;

  Future<void> _retryCamera() async {
    try {
      await controller.start();
    } on MobileScannerException {
      // The controller exposes the native error to errorBuilder below.
    }
  }

  void _accept(BarcodeCapture capture) {
    if (returned) return;
    for (final barcode in capture.barcodes) {
      final value = barcode.rawValue;
      if (value != null && value.isNotEmpty) {
        returned = true;
        Navigator.pop(context, value);
        return;
      }
    }
  }

  Future<void> _readImage() async {
    if (readingImage || returned) return;
    setState(() {
      readingImage = true;
      imageError = null;
    });
    try {
      final result = await FilePicker.pickFile(type: FileType.image);
      if (!mounted || result == null) return;
      final path = result.path;
      if (path == null) {
        setState(() => imageError = '无法读取这张图片，请重新选择。');
        return;
      }
      final capture = await controller.analyzeImage(path);
      if (!mounted) return;
      if (capture == null ||
          capture.barcodes.every((b) => b.rawValue?.isNotEmpty != true)) {
        setState(() => imageError = '没有识别到二维码，请选择完整、清晰的邀请图片。');
        return;
      }
      _accept(capture);
    } on Exception {
      if (mounted) setState(() => imageError = '这张图片无法识别，请换一张图片或使用相机扫描。');
    } finally {
      if (mounted) setState(() => readingImage = false);
    }
  }

  @override
  void dispose() {
    unawaited(controller.dispose());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('扫描曜核邀请')),
    body: SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(24, 16, 24, 24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              '对准电脑上的邀请二维码',
              style: TextStyle(fontSize: 22, fontWeight: FontWeight.w500),
            ),
            const SizedBox(height: 8),
            const Text(
              '在电脑的曜核管理界面生成邀请。手机和电脑需要连接同一网络。',
              style: TextStyle(color: Color(0xff697183), height: 1.5),
            ),
            const SizedBox(height: 24),
            Expanded(
              child: ClipRRect(
                borderRadius: BorderRadius.circular(24),
                child: Stack(
                  fit: StackFit.expand,
                  children: [
                    MobileScanner(
                      controller: controller,
                      onDetect: (capture) {
                        if (!readingImage) _accept(capture);
                      },
                      errorBuilder: (context, error) => ColoredBox(
                        color: const Color(0xffedf0f6),
                        child: Center(
                          child: Padding(
                            padding: const EdgeInsets.all(24),
                            child: Column(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                const Icon(
                                  Icons.camera_alt_outlined,
                                  size: 40,
                                  color: Color(0xff697183),
                                ),
                                const SizedBox(height: 16),
                                Text(
                                  error.errorCode ==
                                          MobileScannerErrorCode
                                              .permissionDenied
                                      ? '相机权限未开启'
                                      : '暂时无法使用相机',
                                  style: const TextStyle(fontSize: 18),
                                ),
                                const SizedBox(height: 8),
                                Text(
                                  error.errorCode ==
                                          MobileScannerErrorCode
                                              .permissionDenied
                                      ? '请在系统设置中允许曜核使用相机，返回后重新扫描。也可以选择保存的邀请图片。'
                                      : '请重试，或选择保存的邀请图片。',
                                  textAlign: TextAlign.center,
                                  style: const TextStyle(
                                    color: Color(0xff697183),
                                    height: 1.5,
                                  ),
                                ),
                                const SizedBox(height: 16),
                                TextButton(
                                  onPressed: _retryCamera,
                                  child: const Text('重新开启相机'),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                    ),
                    ValueListenableBuilder<MobileScannerState>(
                      valueListenable: controller,
                      builder: (context, state, _) => state.error != null
                          ? const SizedBox.shrink()
                          : const IgnorePointer(
                              child: Center(
                                child: Icon(
                                  Icons.qr_code_scanner,
                                  size: 196,
                                  color: Color(0x99ffffff),
                                ),
                              ),
                            ),
                    ),
                  ],
                ),
              ),
            ),
            if (imageError != null)
              Padding(
                padding: const EdgeInsets.only(top: 16),
                child: Text(
                  imageError!,
                  style: const TextStyle(color: Color(0xffa33a3a)),
                ),
              ),
            const SizedBox(height: 20),
            if (!kIsWeb)
              OutlinedButton.icon(
                onPressed: readingImage ? null : _readImage,
                icon: const Icon(Icons.image_outlined),
                label: Text(readingImage ? '正在识别…' : '从图片识别邀请'),
                style: OutlinedButton.styleFrom(
                  minimumSize: const Size(48, 52),
                ),
              ),
          ],
        ),
      ),
    ),
  );
}
