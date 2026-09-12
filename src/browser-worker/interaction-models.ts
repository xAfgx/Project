import type { Locator } from "./types";
import type { InteractionOutcomeExpectation, InteractionReadinessPolicy } from "./interaction-policies";

export interface InteractionPoint {
  x: number;
  y: number;
}

export interface InteractionBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface InteractionTargetState {
  visible: boolean;
  enabled: boolean;
  stable: boolean;
  box?: InteractionBox;
}

export interface PointerInteractionProfile {
   minSteps: number;
   maxSteps: number;
   minStepDelayMs: number;
   maxStepDelayMs: number;
   targetInsetRatio: number;
   targetVariationRatio: number;
   // Human behavior cloning parameters
   bezierMicroJitter: number;       // Biological tremor amplitude (pixels)
   overshootCorrection: boolean;    // Add overshoot and correction
   accelerationProfile: "natural" | "linear" | "ease"; // Biological acceleration
   stoppingOscillation: boolean;    // Mouse oscillation when stopping
   pauseVarianceMs: number;        // Variance in step timing (biological)
   // Deliberate hesitation around a click so navigation-triggering clicks are
   // not instantaneous. Human reaction/settle time before and after pressing.
   preClickPauseMinMs: number;
   preClickPauseMaxMs: number;
   postClickPauseMinMs: number;
   postClickPauseMaxMs: number;
}

export interface FormInteractionProfile {
   readinessTimeoutMs: number;
   verifyTimeoutMs: number;
   // Human typing parameters
   interKeystrokeMinMs: number;     // Minimum delay between keystrokes
   interKeystrokeMaxMs: number;     // Maximum delay between keystrokes
   burstPauseProbability: number;   // Probability of pause between bursts
   burstLengthMin: number;          // Min characters per burst
   burstLengthMax: number;          // Max characters per burst
   thinkPauseBeforeMs: number;      // Think pause before filling
}

export interface InteractionProfiles {
   pointer: PointerInteractionProfile;
   form: FormInteractionProfile;
}

export interface BehavioralProfile {
   movement: {
      enableMicroJitter: boolean;
      enableOvershoot: boolean;
      enableStoppingOscillation: boolean;
      accelerationProfile: "natural";
      jitterAmplitude: number;
      minDurationMs: number;
      maxDurationMs: number;
   };
   typing: {
      enableBurstPattern: boolean;
      minInterKeystrokeMs: number;
      maxInterKeystrokeMs: number;
      burstPauseProbability: number;
      enableThinkPause: boolean;
      thinkPauseMinMs: number;
      thinkPauseMaxMs: number;
   };
   pauses: {
      quickMinMs: number;
      quickMaxMs: number;
      moderateMinMs: number;
      moderateMaxMs: number;
      importantMinMs: number;
      importantMaxMs: number;
      criticalMinMs: number;
      criticalMaxMs: number;
   };
}

export type InteractionFailureReason =
   | "not-ready"
   | "action-error"
   | "outcome-timeout";

export interface InteractionAttemptTrace {
   attempt: number;
   seed: string;
   readinessPolicy: string;
   targetState: InteractionTargetState;
   targetPoint?: InteractionPoint;
   outcomeExpectation?: string;
   failureReason?: InteractionFailureReason;
   error?: string;
}

export interface BaseInteractionOptions {
   seed?: number | string;
   attempts?: number;
   readinessTimeoutMs?: number;
   verifyTimeoutMs?: number;
   readiness?: InteractionReadinessPolicy;
   expected?: InteractionOutcomeExpectation;
   // Human behavior options
   enableHumanBehavior?: boolean;
   thinkPause?: "quick" | "moderate" | "important" | "critical";
   thinkPausePhase?: string;
}

export interface ClickInteractionOptions extends BaseInteractionOptions {
   button?: "left" | "right" | "middle";
   clickCount?: number;
}

export interface FillInteractionOptions extends BaseInteractionOptions {}
export interface TypeInteractionOptions extends BaseInteractionOptions {
   /** Lower bound for the delay between keystrokes in milliseconds. */
   interKeyDelayMinMs?: number;
   /** Upper bound for the delay between keystrokes in milliseconds. */
   interKeyDelayMaxMs?: number;
   /** Clear the field before typing. Defaults to false. */
   clear?: boolean;
   /** Dispatch a native click on the field before typing. */
   click?: boolean;
   /** Probability (0..1) of introducing one typo and correcting it. */
   typoProbability?: number;
}
export interface SelectInteractionOptions extends BaseInteractionOptions {}
export interface FocusInteractionOptions extends BaseInteractionOptions {}
export interface HoverInteractionOptions extends BaseInteractionOptions {}
export interface ScrollInteractionOptions extends BaseInteractionOptions {}

export interface InteractionAttemptResult {
   success: boolean;
   attempts: number;
   targetState: InteractionTargetState;
   targetPoint?: InteractionPoint;
   failureReason?: InteractionFailureReason;
   trace: InteractionAttemptTrace[];
}

export interface InteractionTarget {
   locator: Locator;
   name?: string;
}

export const DEFAULT_INTERACTION_PROFILES: InteractionProfiles = {
   pointer: {
      minSteps: 8,
      maxSteps: 18,
      minStepDelayMs: 2,
      maxStepDelayMs: 9,
      targetInsetRatio: 0.14,
      targetVariationRatio: 0.28,
      // Human behavior cloning defaults
      bezierMicroJitter: 1.5,
      overshootCorrection: true,
      accelerationProfile: "natural",
      stoppingOscillation: true,
      pauseVarianceMs: 4,
      preClickPauseMinMs: 180,
      preClickPauseMaxMs: 520,
      postClickPauseMinMs: 220,
      postClickPauseMaxMs: 640
   },
   form: {
      readinessTimeoutMs: 4_000,
      verifyTimeoutMs: 1_500,
      // Human typing defaults
      interKeystrokeMinMs: 80,
      interKeystrokeMaxMs: 250,
      burstPauseProbability: 0.3,
      burstLengthMin: 3,
      burstLengthMax: 7,
      thinkPauseBeforeMs: 500
   }
};

export const DEFAULT_BEHAVIORAL_PROFILE: BehavioralProfile = {
   movement: {
      enableMicroJitter: true,
      enableOvershoot: true,
      enableStoppingOscillation: true,
      accelerationProfile: "natural",
      jitterAmplitude: 1.5,
      minDurationMs: 300,
      maxDurationMs: 1500
   },
   typing: {
      enableBurstPattern: true,
      minInterKeystrokeMs: 80,
      maxInterKeystrokeMs: 250,
      burstPauseProbability: 0.3,
      enableThinkPause: true,
      thinkPauseMinMs: 200,
      thinkPauseMaxMs: 500
   },
   pauses: {
      quickMinMs: 150,
      quickMaxMs: 600,
      moderateMinMs: 400,
      moderateMaxMs: 1200,
      importantMinMs: 1200,
      importantMaxMs: 3000,
      criticalMinMs: 2000,
      criticalMaxMs: 5000
   }
};
