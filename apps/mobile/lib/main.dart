import 'package:flutter/material.dart';
import 'data/mobile_store.dart';
import 'ui/companion_app.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(CompanionApp(store: MobileStore()..initialize()));
}
