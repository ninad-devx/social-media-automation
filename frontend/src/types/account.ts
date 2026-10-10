export interface Account {
  id: string;
  name: string;
  type: string;
  logo: string | null;
  color: string | null;
  active: boolean;
  channel_count: number;
}

export interface AccountDetail extends Account {
  description: string | null;
  created_at: string | null;
  updated_at: string | null;
  channels: Channel[];
}

import type { Channel } from "./channel";
export type { Channel };