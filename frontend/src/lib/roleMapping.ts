export type InternalRole =
  | "management"
  | "label_manager"
  | "label_ar_manager"
  | "ar_manager"
  | "product_manager"
  | "artist_manager"
  | "dj"
  | "producer";

const ROLE_TO_INTERNAL: Record<string, InternalRole> = {
  ceo_founder: "management",
  management: "management",
  intern: "management",
  label_manager: "label_manager",
  label_ar_manager: "label_ar_manager",
  ar_manager: "ar_manager",
  marketing_manager: "label_manager",
  social_media_manager: "label_manager",
  streaming_manager: "product_manager",
  distribution_manager: "product_manager",
  publishing_manager: "management",
  finance_manager: "management",
  legal_rights_manager: "management",
  artist_manager: "artist_manager",
  artist: "management",
  dj: "dj",
  producer: "producer",
  dj_producer: "dj",
  songwriter: "producer",
  mix_master_engineer: "producer",
};

export function mapRoleToInternal(roleId: string): InternalRole {
  return ROLE_TO_INTERNAL[roleId] ?? "management";
}
