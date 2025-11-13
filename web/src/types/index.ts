import { EventSchema } from "./eventType";

import { z } from "zod";

export const StatSchema = z.object({
  spd: z.number(),
  sta: z.number(),
  pwr: z.number(),
  guts: z.number(),
  wit: z.number(),
});

export const SkillSchema = z.object({
  is_auto_buy_skill: z.boolean(),
  skill_pts_check: z.number(),
  skill_list: z.array(z.string()),
});

export const RaceScheduleSchema = z.object({
  name: z.string(),
  year: z.string(),
  date: z.string(),
});

export const DebugSchema = z.object({
  stop_after_stat_read: z.boolean(),
  hover_stat_regions: z.boolean(),
});

export const PlatformBoundsSchema = z.object({
  x: z.number(),
  y: z.number(),
  width: z.number(),
  height: z.number(),
});

export const MacBluestacksAirSchema = z.object({
  process_name: z.string(),
  window_name: z.string(),
  set_bounds: z.boolean(),
  bounds: PlatformBoundsSchema,
  post_focus_delay: z.number(),
  apply_offset_x: z.boolean(),
  offset_x: z.number(),
  apply_offset_y: z.boolean(),
  offset_y: z.number(),
  apply_recognition_offset: z.boolean(),
  recognition_offset_x: z.number(),
  recognition_offset_y: z.number(),
});

export const PlatformSchema = z.object({
  profile: z.string(),
  mac_bluestacks_air: MacBluestacksAirSchema,
});

export const ConfigSchema = z.object({
  config_name: z.string(),
  priority_stat: z.array(z.string()),
  priority_weights: z.array(z.number()),
  sleep_time_multiplier: z.number(),
  skip_training_energy: z.number(),
  never_rest_energy: z.number(),
  skip_infirmary_unless_missing_energy: z.number(),
  priority_weight: z.string(),
  minimum_mood: z.string(),
  minimum_mood_junior_year: z.string(),
  maximum_failure: z.number(),
  prioritize_g1_race: z.boolean(),
  cancel_consecutive_race: z.boolean(),
  retry_failed_race: z.boolean(),
  position_selection_enabled: z.boolean(),
  enable_positions_by_race: z.boolean(),
  preferred_position: z.string(),
  positions_by_race: z.object({
    sprint: z.string(),
    mile: z.string(),
    medium: z.string(),
    long: z.string(),
  }),
  race_schedule: z.array(RaceScheduleSchema),
  stat_caps: StatSchema,
  skill: SkillSchema,
  event: EventSchema,
  window_name: z.string(),
  debug: DebugSchema.optional(),
  platform: PlatformSchema.optional(),
});

export type Stat = z.infer<typeof StatSchema>;
export type Skill = z.infer<typeof SkillSchema>;
export type RaceScheduleType = z.infer<typeof RaceScheduleSchema>;
export type DebugConfig = z.infer<typeof DebugSchema>;
export type PlatformConfig = z.infer<typeof PlatformSchema>;
export type Config = z.infer<typeof ConfigSchema>;

export type UpdateConfigType = <K extends keyof Config>(
  key: K,
  value: Config[K]
) => void;
