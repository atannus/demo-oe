import { Module } from '@nestjs/common';
import { MongooseModule } from '@nestjs/mongoose';
import { Position, PositionSchema } from './position.schema';
import { Outbox, OutboxSchema } from './outbox.schema';
import { PositionController } from './position.controller';
import { PositionService } from './position.service';
import { PositionGateway } from './position.gateway';
import { OutboxRelayService } from './outbox-relay.service';
import { RedisProvider } from '../redis.provider';
import { PartitionService } from './partition.service';
import { AdminController } from './admin.controller';

@Module({
  imports: [
    MongooseModule.forFeature([
      { name: Position.name, schema: PositionSchema },
      { name: Outbox.name, schema: OutboxSchema },
    ]),
  ],
  controllers: [PositionController, AdminController],
  providers: [PositionService, PositionGateway, OutboxRelayService, RedisProvider, PartitionService],
})
export class PositionModule {}
