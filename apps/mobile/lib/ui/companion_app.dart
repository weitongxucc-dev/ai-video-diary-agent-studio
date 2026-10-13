import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../data/mobile_store.dart';
import '../data/local_media_draft.dart';
import '../data/local_media_draft_controller.dart';
import '../data/voice_sample_controller.dart';
import 'companion_surfaces.dart';
import 'companion_materials_page.dart';
import 'companion_progress_page.dart';
import 'companion_account_page.dart';
import 'material_review_sheet.dart';
import 'debug_connection_sheet.dart';
import 'companion_navigation_bar.dart';
import 'connection_sheet.dart';
import 'voice_sample_sheet.dart';

class CompanionApp extends StatelessWidget {
  const CompanionApp({super.key, required this.store});
  final MobileStore store;
  @override
  Widget build(BuildContext context) => MaterialApp(
    debugShowCheckedModeBanner: false,
    title: '曜核',
    theme: ThemeData(
      useMaterial3: true,
      colorScheme: ColorScheme.fromSeed(
        seedColor: blue,
        primary: blue,
        surface: Colors.white,
      ),
      scaffoldBackgroundColor: background,
      fontFamily: 'PingFang SC',
      appBarTheme: const AppBarTheme(
        backgroundColor: background,
        foregroundColor: ink,
        centerTitle: false,
        scrolledUnderElevation: 0,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: background,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(14),
          borderSide: BorderSide.none,
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          minimumSize: const Size(48, 48),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
          ),
        ),
      ),
    ),
    home: CompanionHome(store: store),
  );
}

class CompanionHome extends StatefulWidget {
  const CompanionHome({super.key, required this.store});
  final MobileStore store;
  @override
  State<CompanionHome> createState() => _CompanionHomeState();
}

class _CompanionHomeState extends State<CompanionHome>
    with WidgetsBindingObserver {
  int tab = 0;
  final notes = TextEditingController();
  final draft = LocalMediaDraftController();
  late final voice = VoiceSampleController(
    sample: () => draft.sample,
    onRecorded: draft.setSample,
  );
  late final homeState = Listenable.merge([store, draft, voice]);
  MobileStore get store => widget.store;
  List<DraftVideo> get files => draft.files;
  bool get choosing => draft.choosing;
  bool get draftReady => draft.ready;
  String? get sample => draft.sample;
  String? get localError => draft.error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    store.setForeground(true);
    unawaited(_restoreDraft());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    store.setForeground(false);
    notes.dispose();
    draft.dispose();
    voice.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    store.setForeground(state == AppLifecycleState.resumed);
    if (state == AppLifecycleState.inactive) {
      unawaited(voice.stopOnInactive());
    }
  }

  Future<void> _restoreDraft() async {
    await draft.initialize();
    if (mounted) notes.text = draft.notes;
  }

  Future<void> _saveDraft() async {
    draft.updateNotes(notes.text);
    await draft.save();
  }

  Future<void> _choose() async {
    if (await draft.choose() && mounted) unawaited(_review());
  }

  Future<void> _review() async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
      ),
      builder: (context) => MaterialReviewSheet(
        files: files,
        notes: notes,
        hasConnection: store.session != null,
        onNotesChanged: draft.updateNotes,
      ),
    );
    await _saveDraft();
  }

  Future<void> _connect() => showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    builder: (context) => ConnectionSheet(
      store: store,
      debugBuilder: (context) => DebugConnectionSheet(store: store),
    ),
  );

  Widget _connectionCard() => HostConnectionCard(
    hasConnection: store.session != null,
    connected: store.connected,
    connectionName: store.session?.name,
    onConnect: _connect,
  );

  Future<void> _disconnect() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('退出当前连接'),
        content: const Text('移除此手机保存的连接记录。素材清单、说明和录音仍保留，主机上的内容不会被删除。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('保留连接'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('确认退出'),
          ),
        ],
      ),
    );
    if (confirmed == true) await store.disconnect();
  }

  Future<void> _voice() async {
    voice.openSheet();
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
      ),
      builder: (context) => ListenableBuilder(
        listenable: Listenable.merge([draft, voice]),
        builder: (context, _) => VoiceSampleSheet(
          recording: voice.recording,
          hasSample: voice.hasSample,
          busy: voice.busy,
          playing: voice.playing,
          seconds: voice.seconds,
          error: voice.error ?? localError,
          onRecord: voice.toggleRecording,
          onPlay: voice.listen,
          onClose: () => Navigator.pop(context),
        ),
      ),
    );
    await voice.closeSheet();
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: homeState,
    builder: (context, _) => DecoratedBox(
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xfff6f7fb), Color(0xfff0f2f7), Color(0xfff7f8fc)],
          stops: [0, .62, 1],
        ),
      ),
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: AppBar(
          backgroundColor: Colors.transparent,
          systemOverlayStyle: const SystemUiOverlayStyle(
            statusBarColor: Colors.transparent,
            statusBarIconBrightness: Brightness.dark,
            statusBarBrightness: Brightness.light,
          ),
          titleSpacing: 22,
          title: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (tab == 0) ...[
                Image.asset(
                  'assets/images/app-icon.png',
                  width: 32,
                  height: 32,
                  excludeFromSemantics: true,
                ),
                const SizedBox(width: 8),
              ],
              Text(
                ['曜核', '进度', '我的'][tab],
                style: const TextStyle(
                  fontSize: 24,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ],
          ),
          actions: [
            Padding(
              padding: const EdgeInsets.only(right: 16),
              child: TextButton.icon(
                onPressed: _connect,
                icon: Icon(
                  store.connected ? Icons.wifi : Icons.wifi_off,
                  size: 18,
                ),
                label: Text(
                  store.connected
                      ? '已连接'
                      : store.session == null
                      ? '连接曜核'
                      : '离线',
                ),
              ),
            ),
          ],
        ),
        body: !store.ready || !draftReady
            ? const Center(child: CircularProgressIndicator())
            : Column(
                children: [
                  Expanded(
                    child: IndexedStack(
                      index: tab,
                      children: [
                        CompanionMaterialsPage(
                          connectionCard: _connectionCard(),
                          files: files,
                          choosing: choosing,
                          active: tab == 0,
                          localError: localError,
                          onChoose: _choose,
                          onReview: _review,
                          onRemove: draft.removeAt,
                        ),
                        CompanionProgressPage(
                          files: files,
                          hasConnection: store.session != null,
                          connected: store.connected,
                          loading: store.loading,
                          error: store.error,
                          lastUpdated: store.lastUpdated,
                          tasks: store.tasks,
                          onRefresh: store.refresh,
                          onConnect: _connect,
                          onPrepare: () => setState(() => tab = 0),
                          onReview: _review,
                        ),
                        CompanionAccountPage(
                          connectionCard: _connectionCard(),
                          hasConnection: store.session != null,
                          hasSample: sample != null,
                          onVoice: _voice,
                          onDisconnect: _disconnect,
                        ),
                      ],
                    ),
                  ),
                  if (tab == 0 && files.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.fromLTRB(22, 9, 22, 2),
                      child: SizedBox(
                        width: double.infinity,
                        child: FilledButton.icon(
                          onPressed: _review,
                          icon: const Icon(Icons.upload_outlined, size: 21),
                          label: Text('确认 ${files.length} 段素材与说明'),
                        ),
                      ),
                    ),
                ],
              ),
        bottomNavigationBar: CompanionNavigationBar(
          index: tab,
          onSelected: (index) => setState(() => tab = index),
        ),
      ),
    ),
  );
}
