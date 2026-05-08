import { forwardRef, Inject, Injectable, ServiceUnavailableException } from '@nestjs/common';
import { InjectModel, InjectConnection } from '@nestjs/mongoose';
import { Connection, Model } from 'mongoose';
import Redis from 'ioredis';
import { Position } from './position.schema';
import { Outbox } from './outbox.schema';
import { REDIS_CLIENT } from '../redis.provider';
import { MetricsService } from '../metrics/metrics.service';
import { PartitionService } from './partition.service';
import { PositionGateway } from './position.gateway';

export const REPLICATION_CHANNEL = 'position:replicated';

const DATA_ID = '1';

@Injectable()
export class PositionService {
  constructor(
    @InjectModel(Position.name) private readonly positionModel: Model<Position>,
    @InjectModel(Outbox.name) private readonly outboxModel: Model<Outbox>,
    @InjectConnection() private readonly connection: Connection,
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
    private readonly metrics: MetricsService,
    private readonly partition: PartitionService,
    @Inject(forwardRef(() => PositionGateway))
    private readonly gateway: PositionGateway,
  ) {}

  async upsert(dataId: string, x: number, y: number) {
    if (this.partition.active && this.partition.mode === 'CP') {
      throw new ServiceUnavailableException('Partition active: CP mode rejects writes');
    }
    const now = new Date();

    const session = await this.connection.startSession();
    try {
      session.startTransaction();
      await this.positionModel.findOneAndUpdate(
        { data_id: dataId },
        { $set: { x, y, updated_at: now } },
        { upsert: true, new: true, session },
      );
      // Array form of create() is required to pass the session option
      await this.outboxModel.create(
        [{ data_id: dataId, x, y, updated_at: now, created_at: new Date() }],
        { session },
      );
      await session.commitTransaction();
    } catch (err) {
      await session.abortTransaction();
      throw err;
    } finally {
      await session.endSession();
    }

    this.gateway.broadcast({ x, y, updated_at: now.toISOString() });
    return { data_id: dataId, x, y, updated_at: now };
  }

  // Called by the gateway when a replication event arrives from FastAPI.
  // No outbox insert — that would create a replication loop.
  async applyReplication(x: number, y: number, updatedAt: string): Promise<void> {
    const ts = new Date(updatedAt);
    await this.positionModel.findOneAndUpdate(
      { data_id: DATA_ID },
      { $set: { x, y, updated_at: ts } },
      { upsert: true, new: true },
    );
    this.gateway.broadcast({ x, y, updated_at: updatedAt });
  }

  async findOne(dataId: string) {
    return this.positionModel.findOne({ data_id: dataId }).lean().exec();
  }
}
