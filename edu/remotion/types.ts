export type WordCue = {
  text: string;
  start: number;
  end: number;
};

export type SceneSpan = {
  id: string;
  from: number;
  duration: number;
};

export type CreditLine = {
  name: string;
  license: string;
  line: string;
};

export type TimelineItem = {
  label: string;
  detail: string;
};

export type EduReelProps = {
  topic: string;
  hook: string;
  concept: string;
  diagramTitle: string;
  diagramNodes: string[];
  timeline: TimelineItem[];
  statValue: string;
  statLabel: string;
  cta: string;
  credits: CreditLine[];
  captions: WordCue[];
  audioFile: string | null;
  imageFile: string | null;
  spokenSeconds: number;
  creditSeconds: number;
  scenes: SceneSpan[];
};
