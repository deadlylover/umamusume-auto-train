import { z } from "zod";

export type SkillData = {
  name: string;
  description: string;
};

export const SkillPresetSchema = z.object({
  name: z.string(),
  skill_list: z.array(z.string()),
});

export const SkillSchema = z.object({
  is_auto_buy_skill: z.boolean(),
  skill_check_turns: z.number(),
  check_skill_before_races: z.boolean(),
  skill_pts_check: z.number(),
  active_preset: z.string().optional(),
  presets: z.array(SkillPresetSchema).optional(),
  skill_list: z.array(z.string()),
});

export type Skill = z.infer<typeof SkillSchema>;
