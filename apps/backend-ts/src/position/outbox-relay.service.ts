import { Injectable, OnModuleInit, OnModuleDestroy, Inject } from '@nestjs/common';
import { InjectModel } from '@nestjs/mongoose';
import { Model, Types } from 'mongoose';
import Redis from 'ioredis';
import { Outbox } from './outbox.schema';
import { REDIS_CLIENT } from '../redis.provider';
import { REPLICATION_CHANNEL } from './position.service';
import { PartitionService } from './partition.service';
import { MetricsService } from '../metrics/metrics.service';

type OutboxDoc = Outbox & { _id: Types.ObjectId };

@Injectable()
export class OutboxRelayService implements OnModuleInit, OnModuleDestroy {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private changeStream: any = null;

  constructor(
    @InjectModel(Outbox.name) private readonly outboxModel: Model<Outbox>,
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
    private readonly partition: PartitionService,
    private readonly metrics: MetricsService,
  ) {}

  async onModuleInit() {
    // Open the stream first so no inserts are missed during the catch-up scan
    this.changeStream = this.outboxModel.watch(
      [{ $match: { operationType: 'insert' } }],
      { fullDocument: 'updateLookup' },
    );

    // Catch-up: publish any docs left from before this process started
    const leftover = await this.outboxModel.find().sort({ created_at: 1 }).exec();
    for (const doc of leftover) {
      await this.relay(doc as unknown as OutboxDoc);
    }

    this.changeStream.on('change', async (event: { fullDocument?: OutboxDoc }) => {
      const doc = event.fullDocument;
      if (!doc) return;
      await this.relay(doc);
    });

    this.changeStream.on('error', async () => {
      setTimeout(() => this.restart(), 2000);
    });
  }

  private async relay(doc: OutboxDoc) {
    if (this.partition.active) {
      // Partition is active (simulated or auto): drop the event.
      // Divergence during a simulated partition is intentional and resolved via the Heal button.
      // During an infrastructure partition, writes to the local DB are preserved; replication
      // resumes from new writes once the partition clears.
      await this.outboxModel.deleteOne({ _id: doc._id });
      return;
    }
    try {
      const payload = {
        source: 'ts',
        x: doc.x,
        y: doc.y,
        updated_at: doc.updated_at.toISOString(),
      };
      await this.redis.publish(REPLICATION_CHANNEL, JSON.stringify(payload));
      this.metrics.redisPublishedTotal.inc();
      await this.outboxModel.deleteOne({ _id: doc._id });
    } catch {
      // Redis publish failed — leave the doc in the outbox.
      // The catch-up scan on next startup will retry delivery.
    }
  }

  private async restart() {
    this.changeStream?.close();
    this.changeStream = null;
    await this.onModuleInit();
  }

  onModuleDestroy() {
    this.changeStream?.close();
  }
}
