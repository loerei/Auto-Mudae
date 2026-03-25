import { useEffect, useRef, useState } from "react";

import { TargetAndTransition, Transition, Variants, useAnimationControls } from "framer-motion";

export const motionTimings = {
  fast: 0.18,
  base: 0.28,
  slow: 0.42
} as const;

export const motionEasings = {
  enter: [0.22, 1, 0.36, 1],
  exit: [0.4, 0, 1, 1],
  emphasis: [0.2, 0.9, 0.2, 1]
} as const;

type Comparator<T> = (prev: T | undefined, next: T) => boolean;

function defaultComparator<T>(prev: T | undefined, next: T): boolean {
  return prev !== undefined && prev !== next;
}

export function buildFadeUp(reduced: boolean, distance = 14): Variants {
  if (reduced) {
    return {
      hidden: { opacity: 0 },
      visible: { opacity: 1, transition: { duration: motionTimings.fast } },
      exit: { opacity: 0, transition: { duration: motionTimings.fast } }
    };
  }
  return {
    hidden: { opacity: 0, y: distance },
    visible: { opacity: 1, y: 0, transition: { duration: motionTimings.base, ease: motionEasings.enter } },
    exit: { opacity: 0, y: -8, transition: { duration: motionTimings.fast, ease: motionEasings.exit } }
  };
}

export function buildFadeIn(reduced: boolean): Variants {
  return {
    hidden: { opacity: 0 },
    visible: { opacity: 1, transition: { duration: motionTimings.base, ease: motionEasings.enter } },
    exit: { opacity: 0, transition: { duration: motionTimings.fast, ease: motionEasings.exit } }
  };
}

export function buildScaleIn(reduced: boolean): Variants {
  if (reduced) return buildFadeIn(reduced);
  return {
    hidden: { opacity: 0, scale: 0.98 },
    visible: { opacity: 1, scale: 1, transition: { duration: motionTimings.base, ease: motionEasings.enter } },
    exit: { opacity: 0, scale: 0.985, transition: { duration: motionTimings.fast, ease: motionEasings.exit } }
  };
}

export function buildHighlightFlash(reduced: boolean): Variants {
  if (reduced) {
    return {
      hidden: { opacity: 0.85 },
      visible: { opacity: 1, transition: { duration: motionTimings.fast } },
      exit: { opacity: 0, transition: { duration: motionTimings.fast } }
    };
  }
  return {
    hidden: { opacity: 0, y: 8 },
    visible: {
      opacity: 1,
      y: 0,
      backgroundColor: ["rgba(251,229,195,0.9)", "rgba(251,229,195,0)"],
      transition: {
        duration: motionTimings.base,
        ease: motionEasings.emphasis
      }
    },
    exit: { opacity: 0, transition: { duration: motionTimings.fast, ease: motionEasings.exit } }
  };
}

export function buildBadgePulse(reduced: boolean): Variants {
  if (reduced) {
    return {
      hidden: { opacity: 0.88 },
      visible: { opacity: 1, transition: { duration: motionTimings.fast } },
      exit: { opacity: 0.88, transition: { duration: motionTimings.fast } }
    };
  }
  return {
    hidden: { opacity: 0.72, scale: 0.98 },
    visible: { opacity: 1, scale: 1, transition: { duration: motionTimings.fast, ease: motionEasings.emphasis } },
    exit: { opacity: 0.72, scale: 0.98, transition: { duration: motionTimings.fast, ease: motionEasings.exit } }
  };
}

export function buildStaggerContainer(reduced: boolean, staggerChildren = 0.05): Variants {
  return {
    hidden: {},
    visible: {
      transition: reduced
        ? { staggerChildren: 0, delayChildren: 0 }
        : { staggerChildren, delayChildren: 0.03 }
    },
    exit: {
      transition: reduced ? {} : { staggerChildren: 0.02, staggerDirection: -1 }
    }
  };
}

export function getTabTransition(reduced: boolean): Variants {
  return buildFadeUp(reduced, 8);
}

export function emphasisAnimation(reduced: boolean, scale = 1.03): TargetAndTransition {
  if (reduced) {
    return {
      opacity: [0.88, 1],
      transition: { duration: motionTimings.fast, ease: motionEasings.enter }
    };
  }
  return {
    scale: [1, scale, 1],
    y: [0, -1, 0],
    transition: { duration: motionTimings.base, ease: motionEasings.emphasis }
  };
}

export function highlightAnimation(reduced: boolean): TargetAndTransition {
  if (reduced) {
    return {
      opacity: [0.86, 1],
      transition: { duration: motionTimings.fast }
    };
  }
  return {
    boxShadow: ["0 0 0 rgba(0,0,0,0)", "0 0 0 6px rgba(251,229,195,0.45)", "0 0 0 rgba(0,0,0,0)"],
    transition: { duration: motionTimings.slow, ease: motionEasings.emphasis }
  };
}

export function usePulseToken<T>(value: T, comparator: Comparator<T> = defaultComparator): number {
  const prevRef = useRef<T | undefined>(undefined);
  const [token, setToken] = useState(0);

  useEffect(() => {
    if (comparator(prevRef.current, value)) {
      setToken((current) => current + 1);
    }
    prevRef.current = value;
  }, [value, comparator]);

  return token;
}

export function usePulseControls<T>(
  value: T,
  reduced: boolean,
  comparator: Comparator<T> = defaultComparator,
  animation: (reducedMotion: boolean) => TargetAndTransition = highlightAnimation
) {
  const controls = useAnimationControls();
  const token = usePulseToken(value, comparator);

  useEffect(() => {
    if (token > 0) {
      void controls.start(animation(reduced));
    }
  }, [animation, controls, reduced, token]);

  return controls;
}

export function isCountdownThresholdPulse(prev: number | null | undefined, next: number | null | undefined): boolean {
  if (prev == null || next == null || prev === next) return false;
  if (next < 0) return false;
  const minuteChanged = prev > 60 && Math.floor(prev / 60) !== Math.floor(next / 60);
  if (minuteChanged) return true;
  return [30, 10, 5, 4, 3, 2, 1].includes(next);
}

export const sharedMotionTransition: Transition = {
  duration: motionTimings.base,
  ease: motionEasings.enter
};
