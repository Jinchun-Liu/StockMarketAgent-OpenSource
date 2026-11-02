import { LegacyRoute } from "../../_instrument/legacy-route";

export async function generateStaticParams(): Promise<Array<{ eventId: string }>> {
  return [{ eventId: "__runtime__" }];
}

export default function EvidencePage() {
  return <LegacyRoute label="独立证据页" />;
}
