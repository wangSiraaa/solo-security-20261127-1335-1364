'use strict';

const { PostStation, S, CERT, PostError } = require('./post');
const { ReplyRoom } = require('./reply_room');
const { Verifier } = require('./verifier');

// Convenience factory wiring a post station to exactly two reply rooms.
function createSystem({ clock } = {}) {
  const post = new PostStation({ clock });
  const rooms = {
    roomA: new ReplyRoom('roomA'),
    roomB: new ReplyRoom('roomB'),
  };
  for (const [name, room] of Object.entries(rooms)) post.attachRoom(name, room);
  const verifier = new Verifier({ post, rooms });
  return { post, rooms, verifier };
}

module.exports = {
  createSystem,
  PostStation,
  ReplyRoom,
  Verifier,
  S,
  CERT,
  PostError,
};
