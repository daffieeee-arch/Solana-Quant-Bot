export type ClosableDashboard = { close(): void | Promise<void> };
export type DestroyableProvider = { destroy(): void | Promise<void> };
export type ClosableOpportunityStorage = { close(): void | Promise<void> };
export type ClosableLedgerStore = { close(): void | Promise<void> };

export type RuntimeResources = {
  dashboard?: ClosableDashboard;
  provider?: DestroyableProvider;
  opportunityStorage?: ClosableOpportunityStorage;
  ledgerStore?: ClosableLedgerStore;
};

/**
 * Stops ingress/network work first, then closes persistence resources.
 * Every initialized resource gets a cleanup attempt even when an earlier one fails.
 */
export async function closeRuntimeResources(resources: RuntimeResources): Promise<void> {
  const failures: unknown[] = [];
  const attempt = async (cleanup: (() => void | Promise<void>) | undefined): Promise<void> => {
    if (!cleanup) return;
    try {
      await cleanup();
    } catch (error) {
      failures.push(error);
    }
  };

  await attempt(resources.dashboard && (() => resources.dashboard!.close()));
  await attempt(resources.provider && (() => resources.provider!.destroy()));
  await attempt(resources.opportunityStorage && (() => resources.opportunityStorage!.close()));
  await attempt(resources.ledgerStore && (() => resources.ledgerStore!.close()));

  if (failures.length === 1) throw failures[0];
  if (failures.length > 1) throw new AggregateError(failures, 'Multiple runtime resources failed to close');
}

/**
 * Owns resources registered during initialization and guarantees cleanup for
 * bounded completion, partial initialization, and runtime failure.
 */
export async function withRuntimeLifecycle<T>(
  operation: (resources: RuntimeResources) => T | Promise<T>,
): Promise<T> {
  const resources: RuntimeResources = {};
  let operationFailed = false;
  let operationError: unknown;
  try {
    return await operation(resources);
  } catch (error) {
    operationFailed = true;
    operationError = error;
    throw error;
  } finally {
    try {
      await closeRuntimeResources(resources);
    } catch (cleanupError) {
      if (operationFailed) {
        const cleanupFailures = cleanupError instanceof AggregateError
          ? cleanupError.errors
          : [cleanupError];
        throw new AggregateError(
          [operationError, ...cleanupFailures],
          'Runtime operation failed and cleanup also failed',
        );
      }
      throw cleanupError;
    }
  }
}

/** Backwards-compatible store-only cleanup wrapper. */
export async function closeRuntimeStores(
  opportunityStorage: ClosableOpportunityStorage,
  ledgerStore: ClosableLedgerStore,
): Promise<void> {
  await closeRuntimeResources({ opportunityStorage, ledgerStore });
}
