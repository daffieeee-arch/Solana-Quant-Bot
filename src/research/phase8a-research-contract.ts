export type ResearchPageQuery = Readonly<{ cursor: number; limit: number }>;

export type ResearchDashboardProvider = Readonly<{
  getSummary(): unknown | Promise<unknown>;
  getEvents(query: ResearchPageQuery): unknown | Promise<unknown>;
  getQuarantines(query: ResearchPageQuery): unknown | Promise<unknown>;
  getProvenance(): unknown | Promise<unknown>;
  getMetrics(): unknown | Promise<unknown>;
  getPrometheus(): string | Promise<string>;
}>;
