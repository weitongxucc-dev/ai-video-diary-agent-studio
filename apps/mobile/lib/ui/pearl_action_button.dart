import 'dart:math' as math;
import 'package:flutter/material.dart';

/// Decorative light, independent of transfer or task progress.
class PearlActionButton extends StatefulWidget {
  const PearlActionButton({
    super.key,
    required this.label,
    required this.onPressed,
    required this.animate,
  });
  final String label;
  final VoidCallback? onPressed;
  final bool animate;
  @override
  State<PearlActionButton> createState() => _PearlActionButtonState();
}

class _PearlActionButtonState extends State<PearlActionButton>
    with SingleTickerProviderStateMixin {
  late final phase = AnimationController(
    vsync: this,
    duration: const Duration(seconds: 6),
  );
  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _updateMotion();
  }

  @override
  void didUpdateWidget(PearlActionButton oldWidget) {
    super.didUpdateWidget(oldWidget);
    _updateMotion();
  }

  void _updateMotion() {
    final enabled =
        widget.animate &&
        widget.onPressed != null &&
        !MediaQuery.disableAnimationsOf(context) &&
        !MediaQuery.accessibleNavigationOf(context);
    if (enabled) {
      if (!phase.isAnimating) phase.repeat();
    } else {
      phase.stop();
      phase.value = .25;
    }
  }

  @override
  void dispose() {
    phase.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => SizedBox(
    width: 196,
    height: 196,
    child: Stack(
      alignment: Alignment.center,
      children: [
        Positioned.fill(
          child: ExcludeSemantics(
            child: IgnorePointer(
              child: RepaintBoundary(
                child: AnimatedBuilder(
                  animation: phase,
                  builder: (context, child) =>
                      CustomPaint(painter: _PearlLight(phase.value)),
                ),
              ),
            ),
          ),
        ),
        Semantics(
          container: true,
          button: true,
          enabled: widget.onPressed != null,
          label: widget.label,
          onTap: widget.onPressed,
          child: Container(
            width: 104,
            height: 104,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              gradient: const LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [
                  Color(0xffffffff),
                  Color(0xfff7f8fb),
                  Color(0xffedf0f6),
                ],
                stops: [0, .36, 1],
              ),
              border: Border.all(
                color: Colors.white.withValues(alpha: .95),
                width: 1.2,
              ),
              boxShadow: [
                BoxShadow(
                  color: const Color(0xff7d91c1).withValues(alpha: .14),
                  blurRadius: 22,
                  offset: const Offset(0, 9),
                ),
                BoxShadow(
                  color: Colors.white.withValues(alpha: .9),
                  blurRadius: 10,
                  offset: const Offset(-3, -4),
                ),
              ],
            ),
            child: Material(
              color: Colors.transparent,
              shape: const CircleBorder(),
              clipBehavior: Clip.antiAlias,
              child: InkWell(
                customBorder: const CircleBorder(),
                onTap: widget.onPressed,
                excludeFromSemantics: true,
                splashColor: const Color(0xff3864f6).withValues(alpha: .06),
                highlightColor: const Color(0xff3864f6).withValues(alpha: .035),
                child: Center(
                  child: ExcludeSemantics(
                    child: Text(
                      widget.label,
                      textAlign: TextAlign.center,
                      style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w400,
                        color: widget.onPressed == null
                            ? const Color(0xff697183)
                            : const Color(0xff30333b),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ],
    ),
  );
}

class _PearlLight extends CustomPainter {
  const _PearlLight(this.phase);
  final double phase;
  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final breath = (1 - math.cos(phase * math.pi * 2)) / 2;
    final haloRadius = 79 + breath * 4;
    final haloRect = Rect.fromCircle(center: center, radius: haloRadius);
    final glow = Paint()
      ..shader = RadialGradient(
        colors: [
          const Color(0xffbed0ff).withValues(alpha: .12 + breath * .06),
          const Color(0xffb8c9fb).withValues(alpha: .17 + breath * .08),
          const Color(0xffb8c9fb).withValues(alpha: .055),
          const Color(0xffb8c9fb).withValues(alpha: 0),
        ],
        stops: const [0, .48, .78, 1],
      ).createShader(haloRect);
    canvas.drawCircle(center, haloRadius, glow);
    final ringRect = Rect.fromCircle(center: center, radius: 69 + breath * 1.5);
    canvas.drawCircle(
      center,
      ringRect.width / 2,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = .7
        ..color = const Color(0xffa8bae7).withValues(alpha: .12),
    );
    final angle = phase * math.pi * 2 - math.pi / 2;
    final rim = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.05
      ..strokeCap = StrokeCap.round
      ..shader = SweepGradient(
        startAngle: 0,
        endAngle: math.pi / 2,
        colors: [
          const Color(0xff879fdd).withValues(alpha: 0),
          const Color(0xff879fdd).withValues(alpha: .18 + breath * .08),
        ],
        transform: GradientRotation(angle),
      ).createShader(ringRect);
    canvas.drawArc(ringRect, angle, math.pi / 2, false, rim);
  }

  @override
  bool shouldRepaint(_PearlLight oldDelegate) => phase != oldDelegate.phase;
}
