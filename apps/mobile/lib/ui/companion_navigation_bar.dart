import 'package:flutter/material.dart';

class CompanionNavigationBar extends StatelessWidget {
  const CompanionNavigationBar({
    super.key,
    required this.index,
    required this.onSelected,
  });
  final int index;
  final ValueChanged<int> onSelected;
  static const labels = ['首页', '进度', '我的'];
  static const icons = [
    Icons.home_outlined,
    Icons.schedule_outlined,
    Icons.person_outline,
  ];
  @override
  Widget build(BuildContext context) {
    final reduced =
        MediaQuery.disableAnimationsOf(context) ||
        MediaQuery.accessibleNavigationOf(context);
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(24, 8, 24, 12),
        child: Center(
          heightFactor: 1,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 320),
            child: Container(
              height: 64,
              padding: const EdgeInsets.all(7),
              decoration: BoxDecoration(
                color: const Color(0xfffefeff),
                borderRadius: BorderRadius.circular(36),
                border: Border.all(color: const Color(0xffe6eaf6), width: .8),
                boxShadow: [
                  BoxShadow(
                    color: const Color(0xff8996b4).withValues(alpha: .065),
                    blurRadius: 20,
                    offset: const Offset(0, 5),
                  ),
                ],
              ),
              child: LayoutBuilder(
                builder: (context, constraints) {
                  final activeWidth = constraints.maxWidth * .43;
                  final restWidth =
                      (constraints.maxWidth - activeWidth - 8) / 2;
                  return Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: List.generate(3, (item) {
                      final selected = item == index;
                      return Semantics(
                        container: true,
                        button: true,
                        selected: selected,
                        label: labels[item],
                        onTap: () => onSelected(item),
                        child: Tooltip(
                          message: labels[item],
                          excludeFromSemantics: true,
                          child: AnimatedContainer(
                            duration: reduced
                                ? Duration.zero
                                : const Duration(milliseconds: 260),
                            curve: Curves.easeOutCubic,
                            width: selected ? activeWidth : restWidth,
                            height: constraints.maxHeight,

                            child: Material(
                              color: selected
                                  ? const Color(0xff3864f6)
                                  : Colors.transparent,
                              animationDuration: Duration.zero,
                              shape: const StadiumBorder(),
                              clipBehavior: Clip.antiAlias,
                              child: InkWell(
                                onTap: () => onSelected(item),
                                customBorder: const StadiumBorder(),
                                excludeFromSemantics: true,
                                child: Padding(
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: 12,
                                  ),
                                  child: Row(
                                    mainAxisAlignment: MainAxisAlignment.center,
                                    children: [
                                      ExcludeSemantics(
                                        child: Icon(
                                          selected && item == 0
                                              ? Icons.home_rounded
                                              : icons[item],
                                          size: 24,
                                          color: selected
                                              ? Colors.white
                                              : const Color(0xff5c616c),
                                        ),
                                      ),
                                      if (selected)
                                        Flexible(
                                          child: Padding(
                                            padding: const EdgeInsets.only(
                                              left: 8,
                                            ),
                                            child: ExcludeSemantics(
                                              child: Text(
                                                labels[item],
                                                maxLines: 1,
                                                softWrap: false,
                                                overflow: TextOverflow.clip,
                                                style: const TextStyle(
                                                  fontSize: 16,
                                                  color: Colors.white,
                                                  fontWeight: FontWeight.w400,
                                                ),
                                              ),
                                            ),
                                          ),
                                        ),
                                    ],
                                  ),
                                ),
                              ),
                            ),
                          ),
                        ),
                      );
                    }),
                  );
                },
              ),
            ),
          ),
        ),
      ),
    );
  }
}
