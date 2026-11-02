import {
  isPremarketBriefingV1_2,
  isPremarketBriefingV1_3,
} from "@/lib/api/contracts";
import { getR1DataProvider } from "@/lib/data/provider";

export type HistoricalExperienceBriefing = Readonly<{
  informationTime: string;
  totalChanges: number;
}>;

export type HistoricalReplaySnapshot = Readonly<{
  briefing: HistoricalExperienceBriefing;
  publishedAt: string;
  lastSuccessfulRunAt: string | null;
}>;

export async function loadHistoricalReplay(): Promise<HistoricalReplaySnapshot | null> {
  const provider = getR1DataProvider();
  const briefingResult = await provider.getPremarketBriefing();
  const briefing = briefingResult.data;
  if (!briefing || (!isPremarketBriefingV1_2(briefing) && !isPremarketBriefingV1_3(briefing))) return null;

  const successfulTimes = (briefing.source_coverage ?? [])
    .map((coverage) => coverage.last_success_at)
    .filter((value): value is string => value !== null);
  const lastSuccessfulRunAt = successfulTimes.length === 0
    ? null
    : successfulTimes.sort((left, right) => Date.parse(right) - Date.parse(left))[0] ?? null;
  return Object.freeze({
    briefing: Object.freeze({
      informationTime: briefing.as_of,
      totalChanges: Object.values(briefing.sections).reduce(
        (total, section) => total + (section.items?.length ?? 0),
        0,
      ),
    }),
    publishedAt: briefing.generated_at,
    lastSuccessfulRunAt,
  });
}
